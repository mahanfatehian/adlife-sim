"""Clean-room release smoke: install a built wheel, drive the whole workflow offline.

The wheel is the canonical distribution, so release verification never happens inside
the development checkout. This script creates a temporary virtual environment, installs
exactly the wheel it was handed, changes to a scratch directory outside the checkout,
and runs the documented workflow — including the verified packaged city catalog,
validated fictional place, campaign, and response inputs, a schema-v6 spatial opportunity,
attention, and rules-only response run, its replay, and its read-only ASGI viewer — validating
the JSON output contract along the way. Every temporary artifact is cleaned up on success and on
failure.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import site
import stat
import subprocess
import sys
import tempfile
from hashlib import sha256
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from typing import Never, TypeAlias, cast

_FICTIONAL_CREATIVE_SHA256 = sha256(b"fictional clean-room creative").hexdigest()
_FICTIONAL_GRID_V2_PROVENANCE: dict[str, object] = {
    "schema_version": 2,
    "city_id": "fictional-grid-v2",
    "name": "Fictional Grid City V2",
    "time_zone": "Etc/UTC",
    "source": {
        "provider": "AdLife Fictional Cartography",
        "dataset": "Bundled fictional grid",
        "version": "2.0.0",
        "published_on": "2026-09-29",
        "source_sha256": "02c6992095203c77308dee9c234d224e609d1942c87dc38bfdc1d9abb4eb7d5f",
        "source_url": "https://example.org/fictional-city",
        "license": "CC0-1.0",
        "attribution": "Fictional demonstration streets; not real map data",
    },
    "source_url": None,
    "license": None,
    "attribution": None,
    "known_omissions": [
        "No measured traffic, population, or land use",
        "No turn restrictions or time-dependent access",
        "Synthetic geometry; not a real city",
    ],
}
_NETWORK_GUARD_LOG = "adlife-smoke-network-guard.log"
_NETWORK_GUARD_TOKEN_ENV = "ADLIFE_SMOKE_GUARD_INVOCATION"
_BASE_ADLIFE_GUARD_TOKENS = (
    "version",
    "doctor",
    "catalog-list",
    "catalog-show",
    "places-validate",
    "campaign-validate",
    "legacy-init",
    "legacy-run",
    "legacy-report",
    "catalog-run",
    "catalog-metrics",
    "catalog-compare",
    "catalog-replay",
)
_SPATIAL_GUARD_TOKENS = (
    "spatial-run-0",
    "spatial-run-1",
    "spatial-metrics-0",
    "spatial-metrics-1",
    "spatial-study",
    "spatial-report",
    "spatial-report-conflict",
)
_ADLIFE_GUARD_TOKENS = _BASE_ADLIFE_GUARD_TOKENS + _SPATIAL_GUARD_TOKENS
_VIEWER_GUARD_TOKEN = "viewer-probe"
_AUTH_AGENT_ENVIRONMENT_NAMES = frozenset(
    {
        "GIT_ASKPASS",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "NETRC",
        "PIP_CONFIG_FILE",
        "PIP_KEYRING_PROVIDER",
        "PYTHON_KEYRING_BACKEND",
        "SSH_AGENT_PID",
        "SSH_ASKPASS",
        "SSH_AUTH_SOCK",
        "UV_CONFIG_FILE",
        "UV_KEYRING_PROVIDER",
    }
)
_INSTALLER_NETWORK_ENVIRONMENT_NAMES = frozenset(
    {
        "ALL_PROXY",
        "FTP_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PIP_EXTRA_INDEX_URL",
        "PIP_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_INDEX",
        "UV_INDEX_URL",
    }
)
_NETWORK_GUARD_SOURCE = '''"""Deny network operations during the installed-wheel smoke."""
import builtins
import ipaddress
import os
import sys
from pathlib import Path


def _is_local_address(address):
    if isinstance(address, (str, bytes)):
        return True
    if not isinstance(address, tuple) or not address or not isinstance(address[0], str):
        return False
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return False


def _deny_network(event, arguments):
    if event in {
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
    }:
        raise RuntimeError(f"installed-wheel smoke blocked network operation: {event}")
    if event in {"socket.bind", "socket.connect", "socket.sendto", "socket.sendmsg"}:
        address = arguments[-1] if arguments else None
        if not _is_local_address(address):
            raise RuntimeError(f"installed-wheel smoke blocked network operation: {event}")


sys.addaudithook(_deny_network)
sys.audit("socket.connect", None, ("127.0.0.1", 1))
sys.audit("socket.sendto", None, ("::1", 1))
sys.audit("socket.sendmsg", None, ("127.0.0.1", 1))
for _event, _arguments in (
    ("socket.connect", (None, ("203.0.113.1", 443))),
    ("socket.sendto", (None, ("203.0.113.1", 443))),
    ("socket.sendmsg", (None, ("203.0.113.1", 443))),
    ("socket.getaddrinfo", ("example.invalid", 443, 0, 0, 0)),
    ("socket.gethostbyname", ("example.invalid",)),
    ("socket.gethostbyname_ex", ("example.invalid",)),
    ("socket.gethostbyaddr", ("203.0.113.1",)),
    ("socket.getnameinfo", (("203.0.113.1", 443), 0)),
):
    try:
        sys.audit(_event, *_arguments)
    except RuntimeError:
        pass
    else:
        raise RuntimeError(f"installed-wheel smoke network self-test failed: {_event}")
builtins.__adlife_smoke_network_guard__ = True
builtins.__adlife_smoke_network_guard_self_test__ = True
_guard_token = os.environ.pop("ADLIFE_SMOKE_GUARD_INVOCATION", "")
if _guard_token and (
    len(_guard_token) > 64
    or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in _guard_token)
):
    raise RuntimeError("installed-wheel smoke guard token is invalid")
with Path(__file__).with_name("adlife-smoke-network-guard.log").open(
    "a", encoding="utf-8"
) as _guard_log:
    _guard_log.write(f"{_guard_token}\\t{sys.argv[0]}\\n")
'''
_VIEWER_PROBE_SOURCE = '''"""Verify the installed schema-v6 viewer without binding a socket."""
from __future__ import annotations

import asyncio
import builtins
import inspect
import json
import sys
from pathlib import Path

def _inside(path: str | Path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root)


def _verify_guard() -> None:
    assert builtins.__adlife_smoke_network_guard__
    assert builtins.__adlife_smoke_network_guard_self_test__
    sys.audit("socket.connect", None, ("127.0.0.1", 1))
    sys.audit("socket.sendto", None, ("::1", 1))
    sys.audit("socket.sendmsg", None, ("127.0.0.1", 1))
    for event, arguments in (
        ("socket.connect", (None, ("203.0.113.1", 443))),
        ("socket.sendto", (None, ("203.0.113.1", 443))),
        ("socket.sendmsg", (None, ("203.0.113.1", 443))),
        ("socket.getaddrinfo", ("example.invalid", 443, 0, 0, 0)),
        ("socket.gethostbyname", ("example.invalid",)),
        ("socket.gethostbyname_ex", ("example.invalid",)),
        ("socket.gethostbyaddr", ("203.0.113.1",)),
        ("socket.getnameinfo", (("203.0.113.1", 443), 0)),
    ):
        try:
            sys.audit(event, *arguments)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"network guard did not deny {event}")


_verify_guard()

import adlife
import httpx

from adlife.city.analysis import metrics_for_stored_city_run
from adlife.city.run_store import CityRunStore
from adlife.city.web import create_city_app
from adlife.core.simulation.spatial_response import (
    SpatialRuleResponse,
    spatial_response_state_document,
    summarize_spatial_response_artifact,
)


async def _json(client: httpx.AsyncClient, path: str) -> dict[str, object]:
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text)
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


async def _main() -> None:
    if len(sys.argv) != 4:
        raise AssertionError("expected root, run identifier, and child site-packages")
    root = Path(sys.argv[1]).resolve()
    run_id = sys.argv[2]
    child_site = Path(sys.argv[3]).resolve()

    origins = (
        Path(adlife.__file__).resolve(),
        Path(inspect.getfile(CityRunStore)).resolve(),
        Path(inspect.getfile(create_city_app)).resolve(),
        Path(inspect.getfile(metrics_for_stored_city_run)).resolve(),
    )
    assert all(_inside(origin, child_site) for origin in origins), origins

    stored = CityRunStore(root).load(run_id)
    assert stored.manifest.schema_version == 6
    assert stored.response_input is not None
    assert stored.response_evaluation is not None
    metrics = metrics_for_stored_city_run(stored)
    application = create_city_app(
        stored.mobility,
        run_id=stored.manifest.run_id,
        run_schema_version=stored.manifest.schema_version,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        spatial_metrics=metrics,
        response_input=stored.response_input,
        response_evaluation=stored.response_evaluation,
    )

    evaluation = stored.response_evaluation
    summary_document = summarize_spatial_response_artifact(evaluation)
    state_document = spatial_response_state_document(evaluation)
    assert evaluation.records
    minute = evaluation.records[0].model_minute
    minute_records = tuple(item for item in evaluation.records if item.model_minute == minute)
    responses = tuple(item for item in minute_records if isinstance(item, SpatialRuleResponse))

    def expected_events(offset: int, limit: int) -> dict[str, object]:
        page = minute_records[offset : offset + limit]
        consumed = offset + len(page)
        return {
            "schema_version": 1,
            "response_model_id": evaluation.model_id,
            "claim_scope": evaluation.claim_scope,
            "minute": minute,
            "agent_id": None,
            "offset": offset,
            "limit": limit,
            "total": len(minute_records),
            "event_type_counts": {
                "spatial.response": sum(
                    item.event_type == "spatial.response" for item in minute_records
                ),
                "spatial.state-updated": sum(
                    item.event_type == "spatial.state-updated" for item in minute_records
                ),
            },
            "channel_counts": {
                "roadside-billboard": sum(
                    item.channel == "roadside-billboard" for item in responses
                ),
                "mobile-feed": sum(item.channel == "mobile-feed" for item in responses),
            },
            "agent_counts": {
                agent.agent_id: sum(
                    item.agent_id == agent.agent_id for item in minute_records
                )
                for agent in stored.mobility.agents
                if any(item.agent_id == agent.agent_id for item in minute_records)
            },
            "next_offset": consumed if consumed < len(minute_records) else None,
            "items": [item.model_dump(mode="json") for item in page],
        }

    def expected_states(offset: int, limit: int) -> dict[str, object]:
        page = state_document.states[offset : offset + limit]
        consumed = offset + len(page)
        return {
            "schema_version": state_document.schema_version,
            "model_id": state_document.model_id,
            "claim_scope": state_document.claim_scope,
            "response_input_sha256": state_document.response_input_sha256,
            "scenario_sha256": state_document.scenario_sha256,
            "city_sha256": state_document.city_sha256,
            "state_scope": "final-end-of-run-not-scrubbed-minute",
            "agent_id": None,
            "offset": offset,
            "limit": limit,
            "total": len(state_document.states),
            "next_offset": consumed if consumed < len(state_document.states) else None,
            "items": [item.model_dump(mode="json") for item in page],
        }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as client:
        metadata = await _json(client, "/api/meta")
        metrics_payload = await _json(client, "/api/spatial-metrics")
        summary = await _json(client, "/api/response-summary")
        first_events = await _json(client, f"/api/response-events?minute={minute}&limit=1")
        later_events = await _json(
            client,
            f"/api/response-events?minute={minute}&offset=1&limit=100",
        )
        first_states = await _json(client, "/api/response-state?limit=1")
        later_states = await _json(client, "/api/response-state?offset=1&limit=100")
        index = await client.get("/")
        javascript = await client.get("/static/app.js")
        stylesheet = await client.get("/static/app.css")

    assert metadata["run_schema_version"] == 6
    assert metadata["response_model_id"] == evaluation.model_id
    assert metadata["response_claim_scope"] == evaluation.claim_scope
    assert metadata["response_count"] == evaluation.counts.response_count
    assert metadata["state_update_count"] == evaluation.counts.state_update_count
    assert metadata["final_state_count"] == len(evaluation.final_states)
    assert metrics_payload == metrics.model_dump(mode="json")
    assert summary == summary_document.model_dump(mode="json")
    assert first_events == expected_events(0, 1)
    assert later_events == expected_events(1, 100)
    assert first_states == expected_states(0, 1)
    assert later_states == expected_states(1, 100)
    assert index.status_code == javascript.status_code == stylesheet.status_code == 200
    assert 'id="response-panel"' in index.text
    assert 'id="response-state-panel"' in index.text
    assert "PURCHASE INTENTION IS NOT PURCHASE PROBABILITY OR SALES" in index.text
    assert "/api/response-summary" in javascript.text
    assert "/api/response-events" in javascript.text
    assert "/api/response-state" in javascript.text
    assert "final-end-of-run-not-scrubbed-minute" in javascript.text
    assert "innerHTML" not in javascript.text
    assert ".response-panel" in stylesheet.text
    assert ".response-state-panel" in stylesheet.text

    print(
        json.dumps(
            {
                "run_schema_version": 6,
                "response_model_id": evaluation.model_id,
                "claim_scope": evaluation.claim_scope,
                "summary_model_id": summary_document.model_id,
                "state_model_id": state_document.model_id,
                "state_scope": "final-end-of-run-not-scrubbed-minute",
                "response_count": evaluation.counts.response_count,
                "final_state_count": len(evaluation.final_states),
                "adlife_module_path": str(origins[0]),
                "child_site_path": str(child_site),
                "module_origin_verified": True,
                "network_guard_verified": True,
                "ui_verified": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


asyncio.run(_main())
'''


def _is_credential_environment_name(name: str) -> bool:
    tokens = tuple(part for part in name.upper().split("_") if part)
    if any(
        token
        in {
            "AUTH",
            "AUTHORIZATION",
            "CREDENTIAL",
            "CREDENTIALS",
            "PASSWD",
            "PASSWORD",
            "SECRET",
            "TOKEN",
        }
        for token in tokens
    ):
        return True
    pairs = set(pairwise(tokens))
    return bool({("ACCESS", "KEY"), ("API", "KEY"), ("PRIVATE", "KEY")} & pairs)


def _is_installer_network_environment_name(name: str) -> bool:
    upper_name = name.upper()
    if upper_name in _INSTALLER_NETWORK_ENVIRONMENT_NAMES:
        return True
    return upper_name.startswith("UV_INDEX_") and upper_name.endswith(("_USERNAME", "_PASSWORD"))


def _subprocess_environment(*, allow_network_environment: bool = False) -> dict[str, str]:
    """Return a deterministic child environment without checkout or credential injection."""
    environment = dict(os.environ)
    exact_names = {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONWARNINGS",
        "PYTHONBREAKPOINT",
        "ADLIFE_DEBUG",
        _NETWORK_GUARD_TOKEN_ENV,
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
        "_CE_CONDA",
        "_CE_M",
    }
    for name in tuple(environment):
        upper_name = name.upper()
        installer_network_name = _is_installer_network_environment_name(upper_name)
        if installer_network_name and allow_network_environment:
            continue
        if (
            upper_name in exact_names
            or upper_name in _AUTH_AGENT_ENVIRONMENT_NAMES
            or upper_name.startswith("PYTHON")
            or upper_name.startswith("CONDA")
            or upper_name.startswith("LD_")
            or upper_name.startswith("DYLD_")
            or installer_network_name
            or _is_credential_environment_name(upper_name)
        ):
            environment.pop(name, None)
    environment.update(
        PYTHONNOUSERSITE="1",
        PYTHONSAFEPATH="1",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    return environment


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    allow_network_environment: bool = False,
    expected_exit: int = 0,
    network_guard_token: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = _subprocess_environment(allow_network_environment=allow_network_environment)
    if network_guard_token is not None:
        if re.fullmatch(r"[a-z0-9-]{1,64}", network_guard_token) is None:
            raise ValueError("smoke network-guard token is invalid")
        environment[_NETWORK_GUARD_TOKEN_ENV] = network_guard_token
    completed = subprocess.run(
        command,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=300,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != expected_exit:
        detail = completed.stdout + completed.stderr
        print(
            f"FAILED ({completed.returncode}, expected {expected_exit}): {' '.join(command)}",
            file=sys.stderr,
        )
        print(detail, file=sys.stderr)
        raise SystemExit(1)
    return completed


def _expect_json(completed: subprocess.CompletedProcess[str], label: str) -> dict[str, object]:
    if completed.stderr:
        _refuse(label, "successful JSON command wrote diagnostics to stderr")
    if not completed.stdout.endswith("\n") or completed.stdout.count("\n") != 1:
        _refuse(label, "stdout must contain exactly one newline-terminated JSON document")
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        print(f"{label}: stdout was not a JSON document: {error}", file=sys.stderr)
        print(completed.stdout[:2000], file=sys.stderr)
        raise SystemExit(1) from error
    if not isinstance(document, dict):
        print(f"{label}: expected a JSON object on stdout", file=sys.stderr)
        raise SystemExit(1)
    return document


def _expect_error_json(
    completed: subprocess.CompletedProcess[str],
    label: str,
    *,
    exit_code: int,
    message: str,
) -> dict[str, object]:
    """Validate one intentional machine-readable refusal without treating it as success."""
    if completed.returncode != exit_code:
        _refuse(label, f"expected exit {exit_code}, got {completed.returncode}")
    if not completed.stdout.endswith("\n") or completed.stdout.count("\n") != 1:
        _refuse(label, "stdout must contain exactly one newline-terminated JSON error")
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        _refuse(label, f"stdout was not a JSON error document: {error}")
    if not isinstance(document, dict):
        _refuse(label, "expected a JSON error object on stdout")
    expected = {
        "error": {
            "exit_code": exit_code,
            "type": "CommandError",
            "message": message,
        }
    }
    if document != expected:
        _refuse(label, "JSON error document did not match the public refusal contract")
    if completed.stderr != f"error: {message}\n":
        _refuse(label, "stderr did not contain the one safe public diagnostic")
    return document


def _verify_offline_doctor(document: dict[str, object]) -> None:
    label = "doctor --offline"
    _exact_keys(document, {"checks", "all_ok"}, label)
    _expect_value(document, "all_ok", True, label)
    checks = document.get("checks")
    expected_names = (
        "package-version",
        "python-version",
        "resources",
        "writable",
        "sqlite-json1",
        "utf-8",
        "providers",
        "offline",
    )
    if not isinstance(checks, list) or len(checks) != len(expected_names):
        _refuse(label, "doctor did not return its exact offline check set")
    for check, name in zip(checks, expected_names, strict=True):
        if not isinstance(check, dict):
            _refuse(label, "doctor check is not an object")
        _exact_keys(check, {"name", "ok", "detail"}, label)
        _expect_value(check, "name", name, label)
        _expect_value(check, "ok", True, label)
        detail = check.get("detail")
        if not isinstance(detail, str) or not detail:
            _refuse(label, "doctor check has no public detail")


def _refuse(label: str, detail: str) -> Never:
    print(f"{label}: {detail}", file=sys.stderr)
    raise SystemExit(1)


def _expect_value(document: dict[str, object], key: str, expected: object, label: str) -> None:
    actual = document.get(key)
    if type(actual) is not type(expected) or actual != expected:
        _refuse(label, f"expected {key}={expected!r}, got {actual!r}")


def _expect_sha256(document: dict[str, object], key: str, label: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _refuse(label, f"{key} is not a SHA-256 digest")
    return value


def _expect_response_funnel(
    *,
    response_count: object,
    state_update_count: object,
    noticed_count: object,
    label: str,
) -> None:
    if (
        type(response_count) is not int
        or type(noticed_count) is not int
        or response_count <= 0
        or response_count != noticed_count
    ):
        _refuse(label, "responses do not match the noticed-attention evidence")
    if (
        type(state_update_count) is not int
        or state_update_count < 1
        or state_update_count > response_count
    ):
        _refuse(label, "response state-update count is invalid")


def _source_artifacts(value: object) -> set[str]:
    artifacts: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_artifacts" and isinstance(item, list):
                artifacts.update(path for path in item if isinstance(path, str))
            else:
                artifacts.update(_source_artifacts(item))
    elif isinstance(value, list):
        for item in value:
            artifacts.update(_source_artifacts(item))
    return artifacts


def _artifact_hashes(directory: Path) -> dict[str, str]:
    """Snapshot every directory and regular file without traversing links/reparse points."""
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    try:
        root_status = directory.lstat()
    except OSError:
        _refuse("city artifact", "expected a real saved run directory")
    if (
        not stat.S_ISDIR(root_status.st_mode)
        or stat.S_ISLNK(root_status.st_mode)
        or getattr(root_status, "st_file_attributes", 0) & reparse_flag
    ):
        _refuse("city artifact", "expected a non-symlink saved run directory")
    hashes: dict[str, str] = {}
    file_count = 0

    def visit(parent: Path, relative: Path) -> None:
        nonlocal file_count
        try:
            entries = sorted(os.scandir(parent), key=lambda entry: entry.name)
        except OSError:
            _refuse("city artifact", "saved run tree could not be inspected safely")
        for entry in entries:
            child_relative = relative / entry.name
            child_key = child_relative.as_posix()
            try:
                child_status = entry.stat(follow_symlinks=False)
            except OSError:
                _refuse("city artifact", "saved run entry could not be inspected safely")
            if (
                stat.S_ISLNK(child_status.st_mode)
                or getattr(child_status, "st_file_attributes", 0) & reparse_flag
            ):
                _refuse("city artifact", "saved run contains a link or reparse point")
            if stat.S_ISDIR(child_status.st_mode):
                hashes[f"{child_key}/"] = "directory"
                visit(Path(entry.path), child_relative)
                continue
            if not stat.S_ISREG(child_status.st_mode):
                _refuse("city artifact", "saved run contains a non-file entry")
            digest = sha256()
            try:
                with Path(entry.path).open("rb") as source:
                    while chunk := source.read(64 * 1024):
                        digest.update(chunk)
            except OSError:
                _refuse("city artifact", "saved run file could not be read safely")
            hashes[child_key] = digest.hexdigest()
            file_count += 1

    visit(directory, Path())
    if file_count == 0:
        _refuse("city artifact", "saved run contains no files")
    return hashes


def _expect_artifacts_unchanged(
    directory: Path,
    expected: dict[str, str],
    boundary: str,
) -> None:
    if _artifact_hashes(directory) != expected:
        _refuse(boundary, "read-only operation modified source city artifacts")


def _canonical_json_bytes(document: dict[str, object], label: str) -> bytes:
    try:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        _refuse(label, f"document cannot be encoded as canonical JSON: {error}")


def _canonical_manifest_document(directory: Path, label: str) -> tuple[dict[str, object], str]:
    path = directory / "run.json"
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    try:
        status = path.lstat()
    except OSError:
        _refuse(label, "canonical run manifest is missing or unsafe")
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or getattr(status, "st_file_attributes", 0) & reparse_flag
    ):
        _refuse(label, "canonical run manifest is missing or unsafe")
    content = path.read_bytes()
    if b"\r" in content or not content.endswith(b"\n") or content.count(b"\n") != 1:
        _refuse(label, "run manifest is not one LF-terminated canonical JSON document")
    try:
        decoded = content[:-1].decode("utf-8", errors="strict")
        document = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _refuse(label, "run manifest is not valid UTF-8 JSON")
    if not isinstance(document, dict) or _canonical_json_bytes(document, label) + b"\n" != content:
        _refuse(label, "run manifest bytes are not canonical JSON")
    return document, sha256(content).hexdigest()


def _canonical_manifest_sha256(directory: Path, label: str) -> str:
    return _canonical_manifest_document(directory, label)[1]


def _exact_keys(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        _refuse(label, "document does not contain its exact public field set")


def _finite_float(value: object, label: str, *, positive_zero: bool = True) -> float:
    if type(value) is not float or not math.isfinite(value):
        _refuse(label, "expected an exact finite float")
    if positive_zero and value == 0.0 and math.copysign(1.0, value) < 0.0:
        _refuse(label, "zero must have a positive sign")
    return value


def _exact_integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _refuse(label, "expected an exact bounded integer")
    return value


def _regular_file_bytes(path: Path, label: str, *, maximum: int) -> bytes:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    try:
        status = path.lstat()
    except OSError:
        _refuse(label, "bound evidence file is missing or unsafe")
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or getattr(status, "st_file_attributes", 0) & reparse_flag
        or status.st_size < 0
        or status.st_size > maximum
    ):
        _refuse(label, "bound evidence file is missing or unsafe")
    try:
        return path.read_bytes()
    except OSError:
        _refuse(label, "bound evidence file could not be read")


def _canonical_json_document(content: bytes, label: str) -> dict[str, object]:
    """Decode one exact canonical, LF-terminated JSON object."""
    if b"\r" in content or not content.endswith(b"\n") or content.count(b"\n") != 1:
        _refuse(label, "evidence is not one LF-terminated canonical JSON document")
    try:
        value = json.loads(content[:-1].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _refuse(label, "evidence is not valid UTF-8 JSON")
    if not isinstance(value, dict) or _canonical_json_bytes(value, label) + b"\n" != content:
        _refuse(label, "evidence bytes are not a canonical JSON object")
    return value


def _canonical_json_lines(
    content: bytes, label: str, *, maximum_records: int
) -> tuple[dict[str, object], ...]:
    """Decode a bounded canonical JSONL stream; an empty stream is valid."""
    if not content:
        return ()
    if b"\r" in content or not content.endswith(b"\n"):
        _refuse(label, "evidence stream is not LF-terminated canonical JSONL")
    lines = content[:-1].split(b"\n")
    if len(lines) > maximum_records or any(not line for line in lines):
        _refuse(label, "evidence stream has an invalid record count or blank record")
    records: list[dict[str, object]] = []
    for line in lines:
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _refuse(label, "evidence stream contains invalid UTF-8 JSON")
        if not isinstance(value, dict) or _canonical_json_bytes(value, label) != line:
            _refuse(label, "evidence stream record is not a canonical JSON object")
        records.append(value)
    return tuple(records)


def _bound_summary(
    run_directory: Path,
    filename: str,
    manifest: dict[str, object],
    manifest_key: str,
    label: str,
) -> dict[str, object]:
    content = _regular_file_bytes(run_directory / "outputs" / filename, label, maximum=1024 * 1024)
    if sha256(content).hexdigest() != manifest[manifest_key]:
        _refuse(label, f"{filename} does not match the canonical manifest")
    return _canonical_json_document(content, label)


def _summary_counts(value: object, names: tuple[str, ...], label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        _refuse(label, "artifact summary counts are not an object")
    _exact_keys(value, {"schema_version", *names}, label)
    _expect_value(value, "schema_version", 1, label)
    return {name: _exact_integer(value.get(name), label) for name in names}


_V6_MANIFEST_FIELDS = {
    "schema_version",
    "run_id",
    "status",
    "model_id",
    "package_version",
    "python_version",
    "city_sha256",
    "agents_sha256",
    "trace_sha256",
    "scenario_sha256",
    "opportunity_stream_sha256",
    "opportunity_summary_sha256",
    "opportunity_stream_bytes",
    "opportunity_count",
    "attention_stream_sha256",
    "attention_summary_sha256",
    "attention_stream_bytes",
    "impression_count",
    "noticed_count",
    "spatial_response_schema_version",
    "spatial_response_model_id",
    "response_input_sha256",
    "response_stream_sha256",
    "response_state_sha256",
    "response_summary_sha256",
    "response_stream_bytes",
    "response_count",
    "state_update_count",
    "response_campaign_count",
    "final_state_count",
    "seed",
    "agent_count",
    "days",
    "frame_count",
    "position_count",
    "city_schema_version",
    "spatial_scenario_schema_version",
    "spatial_opportunity_schema_version",
    "spatial_attention_schema_version",
    "spatial_attention_model_id",
    "place_schema_version",
    "place_set_sha256",
    "place_assignments_sha256",
}
_V6_HASH_FIELDS = {
    "city_sha256",
    "agents_sha256",
    "trace_sha256",
    "scenario_sha256",
    "opportunity_stream_sha256",
    "opportunity_summary_sha256",
    "attention_stream_sha256",
    "attention_summary_sha256",
    "response_input_sha256",
    "response_stream_sha256",
    "response_state_sha256",
    "response_summary_sha256",
}
_V6_CITY_RUN_FIELDS = {
    "run_id",
    "city_id",
    "city_sha256",
    "trace_sha256",
    "frame_count",
    "position_count",
    "run_schema_version",
    "directory",
    "place_set_sha256",
    "place_assignments_sha256",
    "scenario_sha256",
    "opportunity_stream_sha256",
    "opportunity_summary_sha256",
    "opportunity_stream_bytes",
    "opportunity_count",
    "opportunity_counts",
    "opportunity_claim_scope",
    "attention_model_id",
    "attention_claim_scope",
    "attention_notice_probability",
    "attention_stream_sha256",
    "attention_summary_sha256",
    "attention_stream_bytes",
    "impression_count",
    "noticed_count",
    "attention_counts",
    "response_model_id",
    "response_claim_scope",
    "response_input_sha256",
    "response_stream_sha256",
    "response_state_sha256",
    "response_summary_sha256",
    "response_stream_bytes",
    "response_count",
    "state_update_count",
    "response_campaign_count",
    "final_state_count",
    "response_counts",
}


def _verify_v6_manifest(document: dict[str, object], label: str) -> None:
    _exact_keys(document, _V6_MANIFEST_FIELDS, label)
    for key, expected in (
        ("schema_version", 6),
        ("status", "completed"),
        ("model_id", "illustrative-road-spatial-response-study-v1"),
        ("spatial_response_schema_version", 1),
        ("spatial_response_model_id", "spatial-response-v1"),
        ("spatial_scenario_schema_version", 1),
        ("spatial_opportunity_schema_version", 1),
        ("spatial_attention_schema_version", 1),
        ("spatial_attention_model_id", "spatial-attention-v1"),
    ):
        _expect_value(document, key, expected, label)
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", run_id) is None:
        _refuse(label, "run identifier is invalid")
    package_version = document.get("package_version")
    python_version = document.get("python_version")
    if not isinstance(package_version, str) or not package_version:
        _refuse(label, "package version receipt is invalid")
    if (
        not isinstance(python_version, str)
        or re.fullmatch(r"3\.(?:11|12|13)\.[0-9]+", python_version) is None
    ):
        _refuse(label, "Python version receipt is invalid")
    for key in _V6_HASH_FIELDS:
        _expect_sha256(document, key, label)
    counts = {
        key: _exact_integer(document.get(key), label)
        for key in (
            "opportunity_stream_bytes",
            "opportunity_count",
            "attention_stream_bytes",
            "impression_count",
            "noticed_count",
            "response_stream_bytes",
            "response_count",
            "state_update_count",
            "response_campaign_count",
            "final_state_count",
            "seed",
            "agent_count",
            "days",
            "frame_count",
            "position_count",
        )
    }
    city_schema_version = document.get("city_schema_version")
    if type(city_schema_version) is not int or city_schema_version not in (1, 2):
        _refuse(label, "city schema receipt is invalid")
    if not 1 <= counts["agent_count"] <= 30 or not 1 <= counts["days"] <= 7:
        _refuse(label, "run dimensions are outside the supported contract")
    if not 1 <= counts["response_campaign_count"] <= 20:
        _refuse(label, "run campaign count is outside the supported contract")
    if (
        counts["frame_count"] != counts["days"] * 1_440
        or counts["position_count"] != counts["frame_count"] * counts["agent_count"]
    ):
        _refuse(label, "run frame/position counts are inconsistent")
    if (
        counts["opportunity_count"] != counts["impression_count"]
        or counts["noticed_count"] > counts["impression_count"]
        or counts["response_count"] != counts["noticed_count"]
        or counts["final_state_count"] != counts["agent_count"] * counts["response_campaign_count"]
    ):
        _refuse(label, "manifest opportunity/attention/response counts are inconsistent")
    updates = counts["state_update_count"]
    responses = counts["response_count"]
    if (responses == 0 and updates != 0) or (responses > 0 and not 1 <= updates <= responses):
        _refuse(label, "manifest state-update count is inconsistent")
    place_values = (
        document.get("place_schema_version"),
        document.get("place_set_sha256"),
        document.get("place_assignments_sha256"),
    )
    if all(value is None for value in place_values):
        return
    if (
        type(place_values[0]) is not int
        or place_values[0] != 1
        or not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in place_values[1:]
        )
    ):
        _refuse(label, "manifest place binding is incomplete")


_OPPORTUNITY_COUNT_NAMES = (
    "roadside_matching_traversal_count",
    "roadside_active_crossing_count",
    "roadside_proximity_passage_count",
    "roadside_approximately_visible_count",
    "phone_eligible_agent_minute_count",
    "phone_successful_draw_count",
    "frequency_capped_candidate_count",
    "roadside_opportunity_count",
    "phone_opportunity_count",
    "opportunity_count",
)
_ATTENTION_COUNT_NAMES = (
    "opportunity_count",
    "impression_count",
    "noticed_count",
    "roadside_impression_count",
    "roadside_noticed_count",
    "phone_impression_count",
    "phone_noticed_count",
)
_RESPONSE_COUNT_NAMES = (
    "response_count",
    "state_update_count",
    "roadside_response_count",
    "phone_response_count",
    "campaign_count",
    "final_state_count",
)


def _verify_spatial_artifact_summaries(
    run_directory: Path,
    manifest: dict[str, object],
    label: str,
) -> tuple[bytes, bytes]:
    """Bind canonical public summaries to their persisted streams and manifest."""
    streams: dict[str, bytes] = {}
    for name, hash_key, bytes_key in (
        ("spatial-opportunities.jsonl", "opportunity_stream_sha256", "opportunity_stream_bytes"),
        ("spatial-attention.jsonl", "attention_stream_sha256", "attention_stream_bytes"),
        ("spatial-responses.jsonl", "response_stream_sha256", "response_stream_bytes"),
    ):
        content = _regular_file_bytes(
            run_directory / "outputs" / name,
            label,
            maximum=64 * 1024 * 1024,
        )
        if sha256(content).hexdigest() != manifest[hash_key] or len(content) != manifest[bytes_key]:
            _refuse(label, f"{name} does not match the canonical manifest")
        streams[name] = content

    state = _regular_file_bytes(
        run_directory / "outputs" / "response-state.json",
        label,
        maximum=16 * 1024 * 1024,
    )
    if sha256(state).hexdigest() != manifest["response_state_sha256"]:
        _refuse(label, "response-state.json does not match the canonical manifest")

    opportunity = _bound_summary(
        run_directory,
        "opportunity-summary.json",
        manifest,
        "opportunity_summary_sha256",
        label,
    )
    _exact_keys(
        opportunity,
        {
            "schema_version",
            "model_id",
            "claim_scope",
            "opportunity_model_id",
            "scenario_sha256",
            "city_sha256",
            "stream_sha256",
            "stream_bytes",
            "counts",
        },
        label,
    )
    for key, expected in (
        ("schema_version", 1),
        ("model_id", "spatial-opportunity-artifact-v1"),
        ("claim_scope", "synthetic-opportunity-not-impression"),
        ("opportunity_model_id", "spatial-opportunity-v1"),
        ("scenario_sha256", manifest["scenario_sha256"]),
        ("city_sha256", manifest["city_sha256"]),
        ("stream_sha256", manifest["opportunity_stream_sha256"]),
        ("stream_bytes", manifest["opportunity_stream_bytes"]),
    ):
        _expect_value(opportunity, key, expected, label)
    opportunity_counts = _summary_counts(
        opportunity.get("counts"),
        _OPPORTUNITY_COUNT_NAMES,
        label,
    )
    if (
        opportunity_counts["opportunity_count"] != manifest["opportunity_count"]
        or opportunity_counts["opportunity_count"]
        != opportunity_counts["roadside_opportunity_count"]
        + opportunity_counts["phone_opportunity_count"]
    ):
        _refuse(label, "opportunity summary count does not match the canonical manifest")

    attention = _bound_summary(
        run_directory,
        "attention-summary.json",
        manifest,
        "attention_summary_sha256",
        label,
    )
    _exact_keys(
        attention,
        {
            "schema_version",
            "model_id",
            "claim_scope",
            "attention_model_id",
            "scenario_sha256",
            "city_sha256",
            "notice_probability",
            "stream_sha256",
            "stream_bytes",
            "counts",
        },
        label,
    )
    for key, expected in (
        ("schema_version", 1),
        ("model_id", "spatial-attention-artifact-v1"),
        ("claim_scope", "synthetic-attention-not-observed-behavior"),
        ("attention_model_id", "spatial-attention-v1"),
        ("scenario_sha256", manifest["scenario_sha256"]),
        ("city_sha256", manifest["city_sha256"]),
        ("notice_probability", 0.5),
        ("stream_sha256", manifest["attention_stream_sha256"]),
        ("stream_bytes", manifest["attention_stream_bytes"]),
    ):
        _expect_value(attention, key, expected, label)
    attention_counts = _summary_counts(
        attention.get("counts"),
        _ATTENTION_COUNT_NAMES,
        label,
    )
    for key in ("opportunity_count", "impression_count", "noticed_count"):
        if attention_counts[key] != manifest[key]:
            _refuse(label, "attention summary count does not match the canonical manifest")
    if (
        attention_counts["impression_count"]
        != attention_counts["roadside_impression_count"]
        + attention_counts["phone_impression_count"]
        or attention_counts["noticed_count"]
        != attention_counts["roadside_noticed_count"] + attention_counts["phone_noticed_count"]
    ):
        _refuse(label, "attention summary channel counts do not add up")

    response = _bound_summary(
        run_directory,
        "response-summary.json",
        manifest,
        "response_summary_sha256",
        label,
    )
    _exact_keys(
        response,
        {
            "schema_version",
            "model_id",
            "claim_scope",
            "response_model_id",
            "response_input_sha256",
            "scenario_sha256",
            "city_sha256",
            "attention_seed",
            "stream_sha256",
            "stream_bytes",
            "state_document_sha256",
            "state_document_bytes",
            "counts",
        },
        label,
    )
    for key, expected in (
        ("schema_version", 1),
        ("model_id", "spatial-response-artifact-v1"),
        ("claim_scope", "synthetic-response-not-observed-behavior"),
        ("response_model_id", "spatial-response-v1"),
        ("response_input_sha256", manifest["response_input_sha256"]),
        ("scenario_sha256", manifest["scenario_sha256"]),
        ("city_sha256", manifest["city_sha256"]),
        ("attention_seed", manifest["seed"]),
        ("stream_sha256", manifest["response_stream_sha256"]),
        ("stream_bytes", manifest["response_stream_bytes"]),
        ("state_document_sha256", manifest["response_state_sha256"]),
        ("state_document_bytes", len(state)),
    ):
        _expect_value(response, key, expected, label)
    response_counts = _summary_counts(
        response.get("counts"),
        _RESPONSE_COUNT_NAMES,
        label,
    )
    for summary_key, manifest_key in (
        ("response_count", "response_count"),
        ("state_update_count", "state_update_count"),
        ("campaign_count", "response_campaign_count"),
        ("final_state_count", "final_state_count"),
    ):
        if response_counts[summary_key] != manifest[manifest_key]:
            _refuse(label, "response summary count does not match the canonical manifest")
    if response_counts["response_count"] != (
        response_counts["roadside_response_count"] + response_counts["phone_response_count"]
    ):
        _refuse(label, "response summary channel counts do not add up")
    return streams["spatial-responses.jsonl"], state


def _verify_city_run_receipt(
    document: dict[str, object],
    manifest: dict[str, object],
    *,
    directory: Path,
    label: str,
) -> None:
    """Bind one exact public city-run receipt to its canonical persisted manifest."""
    _exact_keys(document, _V6_CITY_RUN_FIELDS, label)
    for receipt_key, manifest_key in (
        ("run_id", "run_id"),
        ("run_schema_version", "schema_version"),
        ("city_sha256", "city_sha256"),
        ("trace_sha256", "trace_sha256"),
        ("frame_count", "frame_count"),
        ("position_count", "position_count"),
        ("place_set_sha256", "place_set_sha256"),
        ("place_assignments_sha256", "place_assignments_sha256"),
        ("scenario_sha256", "scenario_sha256"),
        ("opportunity_stream_sha256", "opportunity_stream_sha256"),
        ("opportunity_summary_sha256", "opportunity_summary_sha256"),
        ("opportunity_stream_bytes", "opportunity_stream_bytes"),
        ("opportunity_count", "opportunity_count"),
        ("attention_model_id", "spatial_attention_model_id"),
        ("attention_stream_sha256", "attention_stream_sha256"),
        ("attention_summary_sha256", "attention_summary_sha256"),
        ("attention_stream_bytes", "attention_stream_bytes"),
        ("impression_count", "impression_count"),
        ("noticed_count", "noticed_count"),
        ("response_model_id", "spatial_response_model_id"),
        ("response_input_sha256", "response_input_sha256"),
        ("response_stream_sha256", "response_stream_sha256"),
        ("response_state_sha256", "response_state_sha256"),
        ("response_summary_sha256", "response_summary_sha256"),
        ("response_stream_bytes", "response_stream_bytes"),
        ("response_count", "response_count"),
        ("state_update_count", "state_update_count"),
        ("response_campaign_count", "response_campaign_count"),
        ("final_state_count", "final_state_count"),
    ):
        _expect_value(document, receipt_key, manifest[manifest_key], label)
    for key, expected in (
        ("city_id", "fictional-grid-v2"),
        ("opportunity_claim_scope", "synthetic-opportunity-not-impression"),
        ("attention_claim_scope", "synthetic-attention-not-observed-behavior"),
        ("attention_notice_probability", 0.5),
        ("response_claim_scope", "synthetic-response-not-observed-behavior"),
    ):
        _expect_value(document, key, expected, label)
    receipt_directory = document.get("directory")
    if (
        not isinstance(receipt_directory, str)
        or not Path(receipt_directory).is_absolute()
        or Path(receipt_directory).resolve() != directory.resolve()
    ):
        _refuse(label, "city-run directory receipt is not the exact contained run directory")

    opportunity_summary = _bound_summary(
        directory,
        "opportunity-summary.json",
        manifest,
        "opportunity_summary_sha256",
        label,
    )
    attention_summary = _bound_summary(
        directory,
        "attention-summary.json",
        manifest,
        "attention_summary_sha256",
        label,
    )
    response_summary = _bound_summary(
        directory,
        "response-summary.json",
        manifest,
        "response_summary_sha256",
        label,
    )
    for key, summary_value in (
        ("opportunity_counts", opportunity_summary.get("counts")),
        ("opportunity_claim_scope", opportunity_summary.get("claim_scope")),
        ("attention_counts", attention_summary.get("counts")),
        ("attention_claim_scope", attention_summary.get("claim_scope")),
        ("attention_notice_probability", attention_summary.get("notice_probability")),
        ("response_counts", response_summary.get("counts")),
        ("response_claim_scope", response_summary.get("claim_scope")),
    ):
        _expect_value(document, key, summary_value, label)

    opportunity_counts = _summary_counts(
        document.get("opportunity_counts"), _OPPORTUNITY_COUNT_NAMES, label
    )
    attention_counts = _summary_counts(
        document.get("attention_counts"), _ATTENTION_COUNT_NAMES, label
    )
    response_counts = _summary_counts(document.get("response_counts"), _RESPONSE_COUNT_NAMES, label)
    for counts, bindings in (
        (opportunity_counts, (("opportunity_count", "opportunity_count"),)),
        (
            attention_counts,
            (
                ("opportunity_count", "opportunity_count"),
                ("impression_count", "impression_count"),
                ("noticed_count", "noticed_count"),
            ),
        ),
        (
            response_counts,
            (
                ("response_count", "response_count"),
                ("state_update_count", "state_update_count"),
                ("campaign_count", "response_campaign_count"),
                ("final_state_count", "final_state_count"),
            ),
        ),
    ):
        for count_key, manifest_key in bindings:
            if counts[count_key] != manifest[manifest_key]:
                _refuse(label, "city-run count summary does not match the canonical manifest")


_RESPONSE_EVENT_NAMES = (
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
)
_RESPONSE_STATE_NAMES = (
    "brand_sentiment",
    "recall_strength",
    "purchase_intention_proxy",
)
_RESPONSE_ARTIFACTS = ["outputs/spatial-responses.jsonl"]
_STATE_ARTIFACTS = ["inputs/spatial-response.json", "outputs/response-state.json"]
_ResponseEventValues: TypeAlias = dict[str, tuple[int | float, int, float]]
_ResponseStateValues: TypeAlias = dict[str, dict[str, int | float]]
_REPORT_RESPONSE_MARKER_COUNTS = {
    "coffee": 0,
    "outdoors": 0,
    "work": 0,
    "price_sensitivity": 1,
    "price sensitivity": 0,
    "novelty_seeking": 2,
    "novelty seeking": 0,
    "advertising_skepticism": 1,
    "advertising skepticism": 0,
    "mobile_recall_encoding": 0,
    "mobile recall encoding": 0,
    "roadside_recall_encoding": 0,
    "roadside recall encoding": 0,
    "impulsivity": 1,
}


def _response_event_receipt(
    value: object,
    *,
    name: str,
    population_size: int,
    label: str,
) -> tuple[int | float, int, float]:
    if not isinstance(value, dict):
        _refuse(label, "response metric receipt is not an object")
    _exact_keys(
        value,
        {
            "schema_version",
            "name",
            "numerator",
            "denominator",
            "value",
            "source_event_types",
            "source_artifacts",
        },
        label,
    )
    _expect_value(value, "schema_version", 1, label)
    _expect_value(value, "name", name, label)
    _expect_value(value, "source_event_types", ["spatial.response"], label)
    _expect_value(value, "source_artifacts", _RESPONSE_ARTIFACTS, label)
    numerator = value.get("numerator")
    count_name = name in {"response_count", "response_reach", "response_frequency"}
    numerator = _exact_integer(numerator, label) if count_name else _finite_float(numerator, label)
    denominator = _exact_integer(value.get("denominator"), label)
    metric_value = _finite_float(value.get("value"), label)
    expected = float(numerator / denominator) if denominator else 0.0
    if metric_value != expected:
        _refuse(label, "response metric value does not match its quotient")
    if name == "response_reach" and int(numerator) > population_size:
        _refuse(label, "response reach exceeds the population")
    return numerator, denominator, metric_value


def _response_state_receipt(value: object, *, name: str, label: str) -> dict[str, int | float]:
    if not isinstance(value, dict):
        _refuse(label, "response state receipt is not an object")
    _exact_keys(
        value,
        {
            "schema_version",
            "name",
            "initial_total",
            "final_total",
            "change_total",
            "denominator",
            "initial_mean",
            "final_mean",
            "mean_change",
            "source_artifacts",
        },
        label,
    )
    _expect_value(value, "schema_version", 1, label)
    _expect_value(value, "name", name, label)
    _expect_value(value, "source_artifacts", _STATE_ARTIFACTS, label)
    numbers: dict[str, int | float] = {
        key: _finite_float(value.get(key), label)
        for key in (
            "initial_total",
            "final_total",
            "change_total",
            "initial_mean",
            "final_mean",
            "mean_change",
        )
    }
    denominator = _exact_integer(value.get("denominator"), label, minimum=1)
    numbers["denominator"] = denominator
    initial = float(numbers["initial_total"])
    final = float(numbers["final_total"])
    change = float(numbers["change_total"])
    if (
        change != final - initial
        or float(numbers["initial_mean"]) != initial / denominator
        or float(numbers["final_mean"]) != final / denominator
        or float(numbers["mean_change"]) != change / denominator
    ):
        _refuse(label, "response state receipt arithmetic is inconsistent")
    lower_bound = -float(denominator) if name == "brand_sentiment" else 0.0
    upper_bound = float(denominator)
    if not lower_bound <= initial <= upper_bound or not lower_bound <= final <= upper_bound:
        _refuse(label, "response state totals exceed their domain bounds")
    return numbers


def _response_series(
    value: object,
    *,
    identity_key: str | None,
    identity: str | None,
    population_size: int,
    include_state: bool,
    label: str,
) -> tuple[dict[str, tuple[int | float, int, float]], dict[str, dict[str, int | float]]]:
    if not isinstance(value, dict):
        _refuse(label, "response metric series is not an object")
    expected = {"schema_version", *_RESPONSE_EVENT_NAMES}
    if identity_key is not None:
        expected.add(identity_key)
    if include_state:
        expected.update(_RESPONSE_STATE_NAMES)
    _exact_keys(value, expected, label)
    _expect_value(value, "schema_version", 1, label)
    if identity_key is not None:
        _expect_value(value, identity_key, identity, label)
    events = {
        name: _response_event_receipt(
            value.get(name), name=name, population_size=population_size, label=label
        )
        for name in _RESPONSE_EVENT_NAMES
    }
    count = events["response_count"]
    reach = events["response_reach"]
    frequency = events["response_frequency"]
    if (
        count[1] != 1
        or reach[1] != population_size
        or frequency[0] != count[0]
        or frequency[1] != reach[0]
        or any(events[name][1] != count[0] for name in _RESPONSE_EVENT_NAMES[3:])
        or not 0 <= reach[0] <= min(count[0], population_size)
        or (count[0] == 0) != (reach[0] == 0)
    ):
        _refuse(label, "response metric series has inconsistent count/reach receipts")
    if (
        not -0.2 <= events["mean_rule_sentiment_delta"][2] <= 0.2
        or not 0.0 <= events["mean_rule_recall_delta"][2] <= 0.3
    ):
        _refuse(label, "direct rule response means exceed their bounds")
    if count[0] == 0 and any(events[name][0] != 0.0 for name in _RESPONSE_EVENT_NAMES[3:]):
        _refuse(label, "zero-response direct numerators are not zero")
    states = (
        {
            name: _response_state_receipt(value.get(name), name=name, label=label)
            for name in _RESPONSE_STATE_NAMES
        }
        if include_state
        else {}
    )
    if count[0] == 0 and any(state["change_total"] != 0.0 for state in states.values()):
        _refuse(label, "zero-response state changed")
    return events, states


def _partitioned_total_matches(total: float, parts: tuple[float, ...]) -> bool:
    regrouped = math.fsum(parts)
    if total == regrouped:
        return True
    if len(parts) <= 1 or all(abs(part) < sys.float_info.min for part in parts):
        return False
    budget = (
        math.fsum((math.ulp(total), math.ulp(regrouped), *(math.ulp(part) for part in parts))) / 2.0
    )
    return abs(total - regrouped) <= budget


def _normalized_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _fold_recorded_response_events(
    records: tuple[dict[str, object], ...],
    *,
    population: int,
) -> _ResponseEventValues:
    count = len(records)
    reach = len({str(record["agent_id"]) for record in records})
    sentiment_total = _normalized_zero(
        math.fsum(cast(float, record["sentiment_delta"]) for record in records)
    )
    recall_total = _normalized_zero(
        math.fsum(cast(float, record["recall_delta"]) for record in records)
    )
    return {
        "response_count": (count, 1, float(count)),
        "response_reach": (reach, population, float(reach / population)),
        "response_frequency": (count, reach, float(count / reach) if reach else 0.0),
        "mean_rule_sentiment_delta": (
            sentiment_total,
            count,
            _normalized_zero(sentiment_total / count) if count else 0.0,
        ),
        "mean_rule_recall_delta": (
            recall_total,
            count,
            _normalized_zero(recall_total / count) if count else 0.0,
        ),
    }


def _response_state_rows(value: object, label: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not value:
        _refuse(label, "response state evidence is not a nonempty list")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            _refuse(label, "response state row is not an object")
        agent_id = item.get("agent_id")
        campaign_id = item.get("campaign_id")
        if not isinstance(agent_id, str) or not isinstance(campaign_id, str):
            _refuse(label, "response state row identity is invalid")
        for field in ("brand_sentiment", "recall_strength", "purchase_intention"):
            _finite_float(item.get(field), label, positive_zero=False)
        rows.append(item)
    return tuple(rows)


def _fold_recorded_response_states(
    initial: tuple[dict[str, object], ...],
    final: tuple[dict[str, object], ...],
) -> _ResponseStateValues:
    receipts: _ResponseStateValues = {}
    for name in _RESPONSE_STATE_NAMES:
        field = "purchase_intention" if name == "purchase_intention_proxy" else name
        initial_total = math.fsum(cast(float, row[field]) for row in initial)
        final_total = math.fsum(cast(float, row[field]) for row in final)
        change_total = _normalized_zero(final_total - initial_total)
        denominator = len(initial)
        receipts[name] = {
            "initial_total": initial_total,
            "final_total": final_total,
            "change_total": change_total,
            "denominator": denominator,
            "initial_mean": initial_total / denominator,
            "final_mean": final_total / denominator,
            "mean_change": _normalized_zero(change_total / denominator),
        }
    return receipts


def _verify_recorded_response_metrics(
    *,
    run_directory: Path,
    manifest: dict[str, object],
    expected_campaign_ids: tuple[str, ...],
    population: int,
    stream: bytes,
    state_content: bytes,
    overall_events: _ResponseEventValues,
    overall_states: _ResponseStateValues,
    channel_series: tuple[_ResponseEventValues, ...],
    campaign_series: list[tuple[_ResponseEventValues, _ResponseStateValues]],
    label: str,
) -> None:
    """Project persisted response/state evidence into every public metric receipt."""
    response_records: list[dict[str, object]] = []
    for record in _canonical_json_lines(stream, label, maximum_records=1_000_000):
        for key, expected in (
            ("schema_version", 1),
            ("model_id", "spatial-response-v1"),
            ("claim_scope", "synthetic-response-not-observed-behavior"),
            ("response_input_sha256", manifest["response_input_sha256"]),
            ("scenario_sha256", manifest["scenario_sha256"]),
            ("city_sha256", manifest["city_sha256"]),
        ):
            _expect_value(record, key, expected, label)
        event_type = record.get("event_type")
        if event_type == "spatial.state-updated":
            continue
        if event_type != "spatial.response":
            _refuse(label, "response stream contains an unknown record type")
        agent_id = record.get("agent_id")
        campaign_id = record.get("campaign_id")
        channel = record.get("channel")
        if (
            not isinstance(agent_id, str)
            or campaign_id not in expected_campaign_ids
            or channel not in {"roadside-billboard", "mobile-feed"}
        ):
            _refuse(label, "response stream metric identity is invalid")
        _finite_float(record.get("sentiment_delta"), label, positive_zero=False)
        _finite_float(record.get("recall_delta"), label, positive_zero=False)
        response_records.append(record)

    input_content = _regular_file_bytes(
        run_directory / "inputs" / "spatial-response.json",
        label,
        maximum=2 * 1024 * 1024,
    )
    response_input = _canonical_json_document(input_content, label)
    if sha256(input_content[:-1]).hexdigest() != manifest["response_input_sha256"]:
        _refuse(label, "response input does not match the canonical manifest")
    for key in ("scenario_sha256", "city_sha256"):
        _expect_value(response_input, key, manifest[key], label)

    state_document = _canonical_json_document(state_content, label)
    _exact_keys(
        state_document,
        {
            "schema_version",
            "model_id",
            "claim_scope",
            "response_input_sha256",
            "scenario_sha256",
            "city_sha256",
            "states",
        },
        label,
    )
    for key, expected in (
        ("schema_version", 1),
        ("model_id", "spatial-response-state-v1"),
        ("claim_scope", "synthetic-response-not-observed-behavior"),
        ("response_input_sha256", manifest["response_input_sha256"]),
        ("scenario_sha256", manifest["scenario_sha256"]),
        ("city_sha256", manifest["city_sha256"]),
    ):
        _expect_value(state_document, key, expected, label)
    initial = _response_state_rows(response_input.get("initial_states"), label)
    final = _response_state_rows(state_document.get("states"), label)
    initial_keys = tuple((str(row["agent_id"]), str(row["campaign_id"])) for row in initial)
    final_keys = tuple((str(row["agent_id"]), str(row["campaign_id"])) for row in final)
    expected_state_count = population * len(expected_campaign_ids)
    if (
        len(initial) != expected_state_count
        or len(final) != expected_state_count
        or len(set(initial_keys)) != len(initial_keys)
        or set(initial_keys) != set(final_keys)
    ):
        _refuse(label, "response state evidence does not contain the complete matching grid")
    state_agents = {agent_id for agent_id, _campaign_id in initial_keys}
    if len(state_agents) != population or any(
        {str(row["agent_id"]) for row in initial if row["campaign_id"] == campaign_id}
        != state_agents
        for campaign_id in expected_campaign_ids
    ):
        _refuse(label, "response state campaigns do not cover one common population")
    if any(str(record["agent_id"]) not in state_agents for record in response_records):
        _refuse(label, "response metric evidence references an agent outside the state grid")

    recorded = tuple(response_records)
    expected_overall_events = _fold_recorded_response_events(recorded, population=population)
    expected_channels = tuple(
        _fold_recorded_response_events(
            tuple(record for record in recorded if record["channel"] == channel),
            population=population,
        )
        for channel in ("roadside-billboard", "mobile-feed")
    )
    expected_campaigns = tuple(
        _fold_recorded_response_events(
            tuple(record for record in recorded if record["campaign_id"] == campaign_id),
            population=population,
        )
        for campaign_id in expected_campaign_ids
    )
    if (
        overall_events != expected_overall_events
        or channel_series != expected_channels
        or tuple(events for events, _states in campaign_series) != expected_campaigns
    ):
        _refuse(label, "response metrics do not project from the persisted response stream")

    expected_overall_states = _fold_recorded_response_states(initial, final)
    expected_campaign_states = tuple(
        _fold_recorded_response_states(
            tuple(row for row in initial if row["campaign_id"] == campaign_id),
            tuple(row for row in final if row["campaign_id"] == campaign_id),
        )
        for campaign_id in expected_campaign_ids
    )
    if (
        overall_states != expected_overall_states
        or tuple(states for _events, states in campaign_series) != expected_campaign_states
    ):
        _refuse(label, "response state metrics do not project from persisted state evidence")


def _verify_response_metrics(
    document: dict[str, object],
    *,
    run_directory: Path,
    expected_campaign_ids: tuple[str, ...],
) -> str:
    """Independently validate and hash one installed response-metrics document."""
    label = f"city-metrics response {run_directory.name}"
    manifest, _manifest_sha256 = _canonical_manifest_document(run_directory, label)
    _verify_v6_manifest(manifest, label)
    _exact_keys(
        document,
        {
            "schema_version",
            "model_id",
            "claim_scope",
            "source_run_schema_version",
            "opportunity_model_id",
            "attention_model_id",
            "response_model_id",
            "scenario_sha256",
            "city_sha256",
            "agents_sha256",
            "trace_sha256",
            "opportunity_structure_sha256",
            "response_input_sha256",
            "response_assumption_structure_sha256",
            "response_stream_sha256",
            "response_state_sha256",
            "seed",
            "population_size",
            "days",
            "campaign_count",
            "overall",
            "channels",
            "campaigns",
        },
        label,
    )
    for key, expected in (
        ("schema_version", 1),
        ("model_id", "spatial-response-metrics-v1"),
        ("claim_scope", "synthetic-response-metrics-not-observed-outcomes"),
        ("source_run_schema_version", 6),
        ("opportunity_model_id", "spatial-opportunity-v1"),
        ("attention_model_id", "spatial-attention-v1"),
        ("response_model_id", "spatial-response-v1"),
        ("scenario_sha256", manifest["scenario_sha256"]),
        ("city_sha256", manifest["city_sha256"]),
        ("agents_sha256", manifest["agents_sha256"]),
        ("trace_sha256", manifest["trace_sha256"]),
        ("response_input_sha256", manifest["response_input_sha256"]),
        ("response_stream_sha256", manifest["response_stream_sha256"]),
        ("response_state_sha256", manifest["response_state_sha256"]),
        ("seed", manifest["seed"]),
        ("population_size", manifest["agent_count"]),
        ("days", manifest["days"]),
        ("campaign_count", manifest["response_campaign_count"]),
    ):
        _expect_value(document, key, expected, label)
    _expect_value(document, "campaign_count", len(expected_campaign_ids), label)
    for key in (
        "scenario_sha256",
        "city_sha256",
        "agents_sha256",
        "trace_sha256",
        "opportunity_structure_sha256",
        "response_input_sha256",
        "response_assumption_structure_sha256",
        "response_stream_sha256",
        "response_state_sha256",
    ):
        _expect_sha256(document, key, label)
    stream, state = _verify_spatial_artifact_summaries(run_directory, manifest, label)
    if (
        sha256(stream).hexdigest() != document["response_stream_sha256"]
        or sha256(state).hexdigest() != document["response_state_sha256"]
        or len(stream) != manifest["response_stream_bytes"]
    ):
        _refuse(label, "response metrics are not bound to the exact persisted evidence")
    population = _exact_integer(document.get("population_size"), label, minimum=1)
    overall_events, overall_states = _response_series(
        document.get("overall"),
        identity_key=None,
        identity=None,
        population_size=population,
        include_state=True,
        label=label,
    )
    channels = document.get("channels")
    if not isinstance(channels, list) or len(channels) != 2:
        _refuse(label, "response metric channels have an invalid public shape")
    channel_series = tuple(
        _response_series(
            value,
            identity_key="channel",
            identity=channel,
            population_size=population,
            include_state=False,
            label=label,
        )[0]
        for value, channel in zip(channels, ("roadside", "mobile"), strict=True)
    )
    campaigns = document.get("campaigns")
    if not isinstance(campaigns, list) or len(campaigns) != len(expected_campaign_ids):
        _refuse(label, "response metric campaigns have an invalid public shape")
    campaign_series: list[
        tuple[dict[str, tuple[int | float, int, float]], dict[str, dict[str, int | float]]]
    ] = []
    for value, campaign_id in zip(campaigns, expected_campaign_ids, strict=True):
        campaign_series.append(
            _response_series(
                value,
                identity_key="campaign_id",
                identity=campaign_id,
                population_size=population,
                include_state=True,
                label=label,
            )
        )
    if overall_events["response_count"][0] != manifest["response_count"]:
        _refuse(label, "response metrics count does not match the manifest")
    for slices in (
        tuple(events for events in channel_series),
        tuple(events for events, _states in campaign_series),
    ):
        if overall_events["response_count"][0] != sum(
            int(item["response_count"][0]) for item in slices
        ):
            _refuse(label, "response slice counts do not add up")
        for mean_name in _RESPONSE_EVENT_NAMES[3:]:
            if not _partitioned_total_matches(
                float(overall_events[mean_name][0]),
                tuple(float(item[mean_name][0]) for item in slices),
            ) or overall_events[mean_name][1] != sum(item[mean_name][1] for item in slices):
                _refuse(label, "response slice mean receipts do not add up")
        overall_reach = int(overall_events["response_reach"][0])
        slice_reaches = tuple(int(item["response_reach"][0]) for item in slices)
        if not max(slice_reaches) <= overall_reach <= sum(slice_reaches):
            _refuse(label, "response slice reach is not a possible union")
    for state_name in _RESPONSE_STATE_NAMES:
        overall_state = overall_states[state_name]
        if overall_state["denominator"] != population * len(expected_campaign_ids):
            _refuse(label, "overall response state does not cover the complete grid")
        campaign_states = tuple(states[state_name] for _events, states in campaign_series)
        if any(item["denominator"] != population for item in campaign_states):
            _refuse(label, "campaign response state does not cover the population")
        for total_name in ("initial_total", "final_total"):
            if not _partitioned_total_matches(
                float(overall_state[total_name]),
                tuple(float(item[total_name]) for item in campaign_states),
            ):
                _refuse(label, "campaign response state totals do not add up")
    _verify_recorded_response_metrics(
        run_directory=run_directory,
        manifest=manifest,
        expected_campaign_ids=expected_campaign_ids,
        population=population,
        stream=stream,
        state_content=state,
        overall_events=overall_events,
        overall_states=overall_states,
        channel_series=channel_series,
        campaign_series=campaign_series,
        label=label,
    )
    return sha256(_canonical_json_bytes(document, label)).hexdigest()


_ATTENTION_NAMES = (
    "opportunity_count",
    "impression_count",
    "noticed_count",
    "opportunity_reach",
    "impression_reach",
    "noticed_reach",
    "impression_frequency",
    "notice_rate",
)


def _expected_observation_sources(campaign_ids: tuple[str, ...]) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    for scope in ("overall", "channel.roadside", "channel.mobile"):
        for name in _ATTENTION_NAMES:
            sources[f"attention.{scope}.{name}"] = [
                "outputs/spatial-opportunities.jsonl"
                if name in {"opportunity_count", "opportunity_reach"}
                else "outputs/spatial-attention.jsonl"
            ]
    for scope in (
        "overall",
        "channel.roadside",
        "channel.mobile",
        *(f"campaign.{campaign_id}" for campaign_id in campaign_ids),
    ):
        for name in _RESPONSE_EVENT_NAMES:
            sources[f"response.{scope}.{name}"] = _RESPONSE_ARTIFACTS
    for scope in ("overall", *(f"campaign.{campaign_id}" for campaign_id in campaign_ids)):
        for name in _RESPONSE_STATE_NAMES:
            for aggregate in ("initial_mean", "final_mean", "mean_change"):
                sources[f"response.{scope}.state.{name}.{aggregate}"] = _STATE_ARTIFACTS
    return dict(sorted(sources.items()))


def _verify_observation(
    value: object,
    *,
    key: str,
    sources: list[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        _refuse(label, "scalar observation is not an object")
    _exact_keys(
        value,
        {"schema_version", "key", "value", "numerator", "denominator", "source_artifacts"},
        label,
    )
    _expect_value(value, "schema_version", 1, label)
    _expect_value(value, "key", key, label)
    _expect_value(value, "source_artifacts", sources, label)
    observed = _finite_float(value.get("value"), label)
    numerator = _finite_float(value.get("numerator"), label)
    denominator = _exact_integer(value.get("denominator"), label)
    expected = numerator / denominator if denominator else 0.0
    if observed != expected:
        _refuse(label, "scalar observation does not match its quotient receipt")
    return value


def _attention_receipt(
    observation: dict[str, object], *, name: str, label: str
) -> dict[str, object]:
    numerator = _finite_float(observation.get("numerator"), label)
    if numerator != int(numerator) or numerator < 0.0:
        _refuse(label, "attention observation numerator is not a nonnegative integer")
    events = {
        "opportunity_count": ["spatial.opportunity"],
        "opportunity_reach": ["spatial.opportunity"],
        "impression_count": ["spatial.impression"],
        "impression_reach": ["spatial.impression"],
        "impression_frequency": ["spatial.impression"],
        "noticed_count": ["spatial.noticed"],
        "noticed_reach": ["spatial.noticed"],
        "notice_rate": ["spatial.impression", "spatial.noticed"],
    }
    return {
        "schema_version": 1,
        "name": name,
        "numerator": int(numerator),
        "denominator": observation["denominator"],
        "value": observation["value"],
        "source_event_types": events[name],
        "source_artifacts": observation["source_artifacts"],
    }


def _attention_metrics_from_observations(
    study: dict[str, object],
    pair: dict[str, object],
    arm: dict[str, object],
    observations: dict[str, dict[str, object]],
    *,
    label: str,
) -> dict[str, object]:
    population = _exact_integer(study.get("population_size"), label, minimum=1)
    series: list[dict[str, object]] = []
    for scope, channel in (
        ("overall", "overall"),
        ("channel.roadside", "roadside"),
        ("channel.mobile", "mobile"),
    ):
        row: dict[str, object] = {"schema_version": 1, "channel": channel}
        for name in _ATTENTION_NAMES:
            row[name] = _attention_receipt(
                observations[f"attention.{scope}.{name}"], name=name, label=label
            )
        count = tuple(row[name] for name in _ATTENTION_NAMES[:3])
        reach = tuple(row[name] for name in _ATTENTION_NAMES[3:6])
        if any(not isinstance(item, dict) for item in (*count, *reach)):
            _refuse(label, "attention receipt reconstruction failed")
        if any(item["denominator"] != 1 for item in count):  # type: ignore[index]
            _refuse(label, "attention count denominator is not one")
        if any(item["denominator"] != population for item in reach):  # type: ignore[index]
            _refuse(label, "attention reach denominator is not the population")
        impression_frequency = row["impression_frequency"]
        notice_rate = row["notice_rate"]
        if not isinstance(impression_frequency, dict) or not isinstance(notice_rate, dict):
            _refuse(label, "attention derived receipt is invalid")
        if (
            count[0]["numerator"] != count[1]["numerator"]  # type: ignore[index]
            or reach[0]["numerator"] != reach[1]["numerator"]  # type: ignore[index]
            or impression_frequency["numerator"] != count[1]["numerator"]  # type: ignore[index]
            or impression_frequency["denominator"] != reach[1]["numerator"]  # type: ignore[index]
            or notice_rate["numerator"] != count[2]["numerator"]  # type: ignore[index]
            or notice_rate["denominator"] != count[1]["numerator"]  # type: ignore[index]
            or int(count[2]["numerator"]) > int(count[1]["numerator"])  # type: ignore[index]
            or int(reach[2]["numerator"]) > int(reach[1]["numerator"])  # type: ignore[index]
        ):
            _refuse(label, "attention count/reach/frequency receipts are inconsistent")
        for count_receipt, reach_receipt in zip(count, reach, strict=True):
            count_numerator = int(count_receipt["numerator"])  # type: ignore[index]
            reach_numerator = int(reach_receipt["numerator"])  # type: ignore[index]
            if not 0 <= reach_numerator <= min(count_numerator, population) or (
                count_numerator == 0
            ) != (reach_numerator == 0):
                _refuse(label, "attention reach is outside its population bounds")
        series.append(row)
    overall, roadside, mobile = series
    for name in _ATTENTION_NAMES[:3]:
        overall_receipt = overall[name]
        roadside_receipt = roadside[name]
        mobile_receipt = mobile[name]
        if (
            not isinstance(overall_receipt, dict)
            or not isinstance(roadside_receipt, dict)
            or not isinstance(mobile_receipt, dict)
            or overall_receipt["numerator"]
            != int(roadside_receipt["numerator"]) + int(mobile_receipt["numerator"])
        ):
            _refuse(label, "attention channel counts do not add up")
    for name in _ATTENTION_NAMES[3:6]:
        receipts = tuple(item[name] for item in series)
        reach_values: list[int] = []
        for receipt in receipts:
            if not isinstance(receipt, dict):
                _refuse(label, "attention reach receipt is invalid")
            reach_values.append(int(receipt["numerator"]))
        overall_reach, roadside_reach, mobile_reach = reach_values
        if (
            not max(roadside_reach, mobile_reach)
            <= overall_reach
            <= (roadside_reach + mobile_reach)
        ):
            _refuse(label, "attention overall reach is not a possible channel union")
    for name, manifest_key in (
        ("opportunity_count", "opportunity_count"),
        ("impression_count", "impression_count"),
        ("noticed_count", "noticed_count"),
    ):
        receipt = overall[name]
        if not isinstance(receipt, dict) or receipt["numerator"] != arm.get(manifest_key):
            _refuse(label, "attention metrics do not match their manifest counts")
    city = study.get("city")
    if not isinstance(city, dict):
        _refuse(label, "study city provenance is invalid")
    return {
        "schema_version": 1,
        "model_id": "spatial-metrics-v1",
        "claim_scope": "synthetic-metrics-not-observed-outcomes",
        "source_run_schema_version": 6,
        "opportunity_model_id": "spatial-opportunity-v1",
        "attention_model_id": "spatial-attention-v1",
        "scenario_sha256": arm["scenario_sha256"],
        "city_sha256": city["city_sha256"],
        "agents_sha256": pair["agents_sha256"],
        "trace_sha256": pair["trace_sha256"],
        "opportunity_structure_sha256": arm["opportunity_structure_sha256"],
        "seed": pair["seed"],
        "population_size": population,
        "days": study["days"],
        "overall": overall,
        "channels": [roadside, mobile],
    }


def _response_metrics_from_observations(
    study: dict[str, object],
    pair: dict[str, object],
    arm: dict[str, object],
    observations: dict[str, dict[str, object]],
    *,
    label: str,
) -> dict[str, object]:
    response = arm.get("response")
    campaign_ids = study.get("campaign_ids")
    city = study.get("city")
    if (
        not isinstance(response, dict)
        or not isinstance(campaign_ids, list)
        or not isinstance(city, dict)
    ):
        _refuse(label, "study response provenance is invalid")
    scopes = (
        "overall",
        "channel.roadside",
        "channel.mobile",
        *(f"campaign.{campaign_id}" for campaign_id in campaign_ids),
    )
    series: list[dict[str, object]] = []
    for scope in scopes:
        row: dict[str, object] = {"schema_version": 1}
        if scope.startswith("channel."):
            row["channel"] = scope.split(".")[1]
        elif scope.startswith("campaign."):
            row["campaign_id"] = scope.split(".")[1]
        for name in _RESPONSE_EVENT_NAMES:
            observation = observations[f"response.{scope}.{name}"]
            numerator = observation["numerator"]
            if name in {"response_count", "response_reach", "response_frequency"}:
                if type(numerator) is not float or numerator != int(numerator) or numerator < 0:
                    _refuse(label, "response count observation is not an integer receipt")
                numerator = int(numerator)
            row[name] = {
                "schema_version": 1,
                "name": name,
                "numerator": numerator,
                "denominator": observation["denominator"],
                "value": observation["value"],
                "source_event_types": ["spatial.response"],
                "source_artifacts": _RESPONSE_ARTIFACTS,
            }
        if not scope.startswith("channel."):
            for state_name in _RESPONSE_STATE_NAMES:
                values = {
                    aggregate: observations[f"response.{scope}.state.{state_name}.{aggregate}"]
                    for aggregate in ("initial_mean", "final_mean", "mean_change")
                }
                denominators = {item["denominator"] for item in values.values()}
                if len(denominators) != 1:
                    _refuse(label, "response state observation denominators differ")
                row[state_name] = {
                    "schema_version": 1,
                    "name": state_name,
                    "initial_total": values["initial_mean"]["numerator"],
                    "final_total": values["final_mean"]["numerator"],
                    "change_total": values["mean_change"]["numerator"],
                    "denominator": values["initial_mean"]["denominator"],
                    "initial_mean": values["initial_mean"]["value"],
                    "final_mean": values["final_mean"]["value"],
                    "mean_change": values["mean_change"]["value"],
                    "source_artifacts": _STATE_ARTIFACTS,
                }
        series.append(row)
    return {
        "schema_version": 1,
        "model_id": "spatial-response-metrics-v1",
        "claim_scope": "synthetic-response-metrics-not-observed-outcomes",
        "source_run_schema_version": 6,
        "opportunity_model_id": "spatial-opportunity-v1",
        "attention_model_id": "spatial-attention-v1",
        "response_model_id": "spatial-response-v1",
        "scenario_sha256": arm["scenario_sha256"],
        "city_sha256": city["city_sha256"],
        "agents_sha256": pair["agents_sha256"],
        "trace_sha256": pair["trace_sha256"],
        "opportunity_structure_sha256": arm["opportunity_structure_sha256"],
        "response_input_sha256": response["response_input_sha256"],
        "response_assumption_structure_sha256": response["response_assumption_structure_sha256"],
        "response_stream_sha256": response["response_stream_sha256"],
        "response_state_sha256": response["response_state_sha256"],
        "seed": pair["seed"],
        "population_size": study["population_size"],
        "days": study["days"],
        "campaign_count": response["response_campaign_count"],
        "overall": series[0],
        "channels": series[1:3],
        "campaigns": series[3:],
    }


def _verify_notice_response_alignment(
    attention: dict[str, object], response: dict[str, object], label: str
) -> None:
    attention_channels = attention.get("channels")
    response_channels = response.get("channels")
    if not isinstance(attention_channels, list) or not isinstance(response_channels, list):
        _refuse(label, "study attention/response channels are invalid")
    attention_series = (attention.get("overall"), *attention_channels)
    response_series = (response.get("overall"), *response_channels)
    for notices, responses in zip(attention_series, response_series, strict=True):
        if not isinstance(notices, dict) or not isinstance(responses, dict):
            _refuse(label, "study attention/response series is invalid")
        for response_name, attention_name in (
            ("response_count", "noticed_count"),
            ("response_reach", "noticed_reach"),
        ):
            response_receipt = responses.get(response_name)
            attention_receipt = notices.get(attention_name)
            if (
                not isinstance(response_receipt, dict)
                or not isinstance(attention_receipt, dict)
                or response_receipt.get("numerator") != attention_receipt.get("numerator")
            ):
                _refuse(label, "study response does not exactly process every notice")


def _verify_study_city_provenance(
    city: dict[str, object], manifest: dict[str, object], label: str
) -> None:
    _exact_keys(
        city,
        {
            "schema_version",
            "city_id",
            "name",
            "city_sha256",
            "time_zone",
            "source",
            "source_url",
            "license",
            "attribution",
            "known_omissions",
        },
        label,
    )
    _expect_value(city, "schema_version", manifest["city_schema_version"], label)
    _expect_value(city, "city_sha256", manifest["city_sha256"], label)
    city_id = city.get("city_id")
    name = city.get("name")
    if (
        not isinstance(city_id, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", city_id) is None
        or not isinstance(name, str)
        or not 1 <= len(name) <= 120
    ):
        _refuse(label, "study city public identity is invalid")
    if city["schema_version"] == 2:
        expected = {**_FICTIONAL_GRID_V2_PROVENANCE, "city_sha256": city["city_sha256"]}
        if city != expected:
            _refuse(label, "fixed v2 city provenance is not the packaged public receipt")
        return
    if (
        city["schema_version"] != 1
        or city.get("time_zone") is not None
        or city.get("source") is not None
        or city.get("known_omissions") != []
    ):
        _refuse(label, "v1 study city provenance invents v2 metadata")
    source_url = city.get("source_url")
    license_name = city.get("license")
    attribution = city.get("attribution")
    if (
        not isinstance(source_url, str)
        or re.fullmatch(r"https://[^\s/@]+(?:/[^\s]*)?", source_url) is None
        or not isinstance(license_name, str)
        or not 1 <= len(license_name) <= 80
        or not isinstance(attribution, str)
        or not 1 <= len(attribution) <= 240
    ):
        _refuse(label, "v1 study city provenance is incomplete or unsafe")


def _verify_spatial_aa_study(
    document: dict[str, object],
    *,
    definition_bytes: bytes,
    run_directories: dict[int, Path],
    response_metrics: dict[int, dict[str, object]],
    response_metrics_sha256: dict[int, str],
    expected_place_set_sha256: str | None = None,
) -> str:
    """Verify the installed two-seed study's complete exact-zero evidence graph."""
    label = "city-study A/A"
    if (
        b"\r" in definition_bytes
        or not definition_bytes.endswith(b"\n")
        or definition_bytes.count(b"\n") != 1
    ):
        _refuse(label, "study definition is not one LF-terminated canonical JSON document")
    try:
        definition = json.loads(definition_bytes[:-1].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _refuse(label, "study definition is not valid UTF-8 JSON")
    if not isinstance(definition, dict) or (
        _canonical_json_bytes(definition, label) + b"\n" != definition_bytes
    ):
        _refuse(label, "study definition bytes are not canonical JSON")
    definition_sha256 = sha256(definition_bytes[:-1]).hexdigest()

    _exact_keys(
        definition,
        {"schema_version", "study_id", "design", "analysis_scope", "pairs"},
        label,
    )
    expected_definition: dict[str, object] = {
        "schema_version": 1,
        "study_id": "wheel-aa-study",
        "design": "a-a",
        "analysis_scope": "attention-and-response",
    }
    for key, expected in expected_definition.items():
        _expect_value(definition, key, expected, label)
    declared_pairs = definition.get("pairs")
    if not isinstance(declared_pairs, list) or len(declared_pairs) != 2:
        _refuse(label, "study definition does not declare exactly two pairs")
    declared_run_ids: dict[int, str] = {}
    for seed, pair in enumerate(declared_pairs):
        if not isinstance(pair, dict):
            _refuse(label, "study definition pair is not an object")
        _exact_keys(pair, {"seed", "control_run_id", "treatment_run_id"}, label)
        run_id = f"catalog-seed-{seed}"
        for key, expected in (
            ("seed", seed),
            ("control_run_id", run_id),
            ("treatment_run_id", run_id),
        ):
            _expect_value(pair, key, expected, label)
        declared_run_ids[seed] = run_id
    definition_sha256 = sha256(definition_bytes[:-1]).hexdigest()

    _exact_keys(
        document,
        {
            "schema_version",
            "model_id",
            "claim_scope",
            "definition",
            "study_definition_sha256",
            "seeds",
            "seed_protocol",
            "evidence_tier",
            "bootstrap_model_id",
            "bootstrap_resamples",
            "bootstrap_confidence",
            "direction_threshold",
            "source_run_schema_version",
            "source_run_model_id",
            "opportunity_model_id",
            "attention_model_id",
            "response_model_id",
            "package_version",
            "python_version",
            "city",
            "city_provenance_sha256",
            "days",
            "population_size",
            "place_schema_version",
            "place_set_sha256",
            "control_scenario_sha256",
            "treatment_scenario_sha256",
            "control_response_input_sha256",
            "treatment_response_input_sha256",
            "control_response_assumption_structure_sha256",
            "treatment_response_assumption_structure_sha256",
            "campaign_ids",
            "opportunity_classification",
            "opportunity_matched_pair_count",
            "opportunity_confounded_pair_count",
            "response_assumption_classification",
            "response_assumption_matched_pair_count",
            "response_assumption_confounded_pair_count",
            "a_a_status",
            "pairs",
            "statistics",
        },
        label,
    )
    expected_identity: dict[str, object] = {
        "schema_version": 1,
        "model_id": "spatial-paired-study-v1",
        "claim_scope": "synthetic-study-not-observed-or-causal-effect",
        "definition": definition,
        "study_definition_sha256": definition_sha256,
        "seeds": [0, 1],
        "seed_protocol": "contiguous-zero-based-seeds-v1",
        "evidence_tier": "exploratory-under-50-seeds",
        "bootstrap_model_id": "spatial-paired-bootstrap-v1",
        "bootstrap_resamples": 10_000,
        "bootstrap_confidence": 0.95,
        "direction_threshold": 0.8,
        "source_run_schema_version": 6,
        "source_run_model_id": "illustrative-road-spatial-response-study-v1",
        "opportunity_model_id": "spatial-opportunity-v1",
        "attention_model_id": "spatial-attention-v1",
        "response_model_id": "spatial-response-v1",
        "opportunity_classification": "matched-opportunity-structure",
        "opportunity_matched_pair_count": 2,
        "opportunity_confounded_pair_count": 0,
        "response_assumption_classification": "matched-response-assumptions",
        "response_assumption_matched_pair_count": 2,
        "response_assumption_confounded_pair_count": 0,
        "a_a_status": "exact-zero-verified",
    }
    for key, expected in expected_identity.items():
        _expect_value(document, key, expected, label)

    if (
        set(run_directories) != {0, 1}
        or set(response_metrics) != {0, 1}
        or set(response_metrics_sha256) != {0, 1}
    ):
        _refuse(label, "study source evidence does not contain exactly seeds zero and one")
    manifests: dict[int, dict[str, object]] = {}
    manifest_hashes: dict[int, str] = {}
    for seed in (0, 1):
        manifest, digest = _canonical_manifest_document(run_directories[seed], label)
        _verify_v6_manifest(manifest, label)
        _expect_value(manifest, "run_id", declared_run_ids[seed], label)
        _expect_value(manifest, "seed", seed, label)
        manifests[seed] = manifest
        manifest_hashes[seed] = digest
    first_manifest = manifests[0]
    city = document.get("city")
    if not isinstance(city, dict):
        _refuse(label, "study city provenance is not an object")
    _verify_study_city_provenance(city, first_manifest, label)
    _expect_value(
        document,
        "city_provenance_sha256",
        sha256(_canonical_json_bytes(city, label)).hexdigest(),
        label,
    )
    for manifest in manifests.values():
        for key, expected in (
            ("package_version", first_manifest["package_version"]),
            ("python_version", first_manifest["python_version"]),
            ("city_sha256", first_manifest["city_sha256"]),
            ("city_schema_version", first_manifest["city_schema_version"]),
            ("agent_count", first_manifest["agent_count"]),
            ("days", first_manifest["days"]),
            ("place_schema_version", first_manifest["place_schema_version"]),
            ("place_set_sha256", first_manifest["place_set_sha256"]),
            ("scenario_sha256", first_manifest["scenario_sha256"]),
            ("response_input_sha256", first_manifest["response_input_sha256"]),
        ):
            _expect_value(manifest, key, expected, label)
    if expected_place_set_sha256 is not None:
        _expect_sha256({"place_set_sha256": expected_place_set_sha256}, "place_set_sha256", label)
        _expect_value(first_manifest, "place_schema_version", 1, label)
        _expect_value(first_manifest, "place_set_sha256", expected_place_set_sha256, label)
        for manifest in manifests.values():
            _expect_sha256(manifest, "place_assignments_sha256", label)
    for key, expected in (
        ("package_version", first_manifest["package_version"]),
        ("python_version", first_manifest["python_version"]),
        ("days", first_manifest["days"]),
        ("population_size", first_manifest["agent_count"]),
        ("place_schema_version", first_manifest["place_schema_version"]),
        ("place_set_sha256", first_manifest["place_set_sha256"]),
        ("control_scenario_sha256", first_manifest["scenario_sha256"]),
        ("treatment_scenario_sha256", first_manifest["scenario_sha256"]),
        ("control_response_input_sha256", first_manifest["response_input_sha256"]),
        ("treatment_response_input_sha256", first_manifest["response_input_sha256"]),
    ):
        _expect_value(document, key, expected, label)
    campaign_ids_value = document.get("campaign_ids")
    if not isinstance(campaign_ids_value, list) or campaign_ids_value != ["fictional-launch"]:
        _refuse(label, "study campaign identities are not the exact smoke contract")
    campaign_ids = tuple(campaign_ids_value)
    assumption_sha256 = response_metrics[0].get("response_assumption_structure_sha256")
    for metrics in response_metrics.values():
        _expect_value(
            metrics,
            "response_assumption_structure_sha256",
            assumption_sha256,
            label,
        )
    for key in (
        "control_response_assumption_structure_sha256",
        "treatment_response_assumption_structure_sha256",
    ):
        _expect_value(document, key, assumption_sha256, label)

    pairs = document.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 2:
        _refuse(label, "study does not contain the exact two declared seed pairs")
    expected_sources = _expected_observation_sources(campaign_ids)
    expected_keys = tuple(expected_sources)
    for seed, pair_value in enumerate(pairs):
        if not isinstance(pair_value, dict):
            _refuse(label, "pair receipt is not a JSON object")
        pair = pair_value
        _exact_keys(
            pair,
            {
                "seed",
                "agents_sha256",
                "trace_sha256",
                "place_assignments_sha256",
                "control",
                "treatment",
                "opportunity_classification",
                "response_assumption_classification",
                "scalars",
            },
            label,
        )
        _expect_value(pair, "seed", seed, label)
        _expect_value(
            pair,
            "opportunity_classification",
            "matched-opportunity-structure",
            label,
        )
        _expect_value(
            pair,
            "response_assumption_classification",
            "matched-response-assumptions",
            label,
        )
        control = pair.get("control")
        treatment = pair.get("treatment")
        if not isinstance(control, dict) or treatment != control:
            _refuse(label, "A/A arm receipts are not exactly identical")
        manifest = manifests[seed]
        direct_metrics = response_metrics[seed]
        direct_hash = _verify_response_metrics(
            direct_metrics,
            run_directory=run_directories[seed],
            expected_campaign_ids=campaign_ids,
        )
        if direct_hash != response_metrics_sha256[seed]:
            _refuse(label, "study did not retain the exact directly certified metrics digest")
        _exact_keys(
            control,
            {
                "run_id",
                "manifest_sha256",
                "scenario_sha256",
                "opportunity_stream_sha256",
                "opportunity_summary_sha256",
                "opportunity_stream_bytes",
                "opportunity_count",
                "attention_stream_sha256",
                "attention_summary_sha256",
                "attention_stream_bytes",
                "impression_count",
                "noticed_count",
                "opportunity_structure_sha256",
                "attention_metrics_sha256",
                "response",
            },
            label,
        )
        for key, expected in (
            ("run_id", declared_run_ids[seed]),
            ("manifest_sha256", manifest_hashes[seed]),
            ("scenario_sha256", manifest["scenario_sha256"]),
            ("opportunity_stream_sha256", manifest["opportunity_stream_sha256"]),
            ("opportunity_summary_sha256", manifest["opportunity_summary_sha256"]),
            ("opportunity_stream_bytes", manifest["opportunity_stream_bytes"]),
            ("opportunity_count", manifest["opportunity_count"]),
            ("attention_stream_sha256", manifest["attention_stream_sha256"]),
            ("attention_summary_sha256", manifest["attention_summary_sha256"]),
            ("attention_stream_bytes", manifest["attention_stream_bytes"]),
            ("impression_count", manifest["impression_count"]),
            ("noticed_count", manifest["noticed_count"]),
            ("opportunity_structure_sha256", direct_metrics["opportunity_structure_sha256"]),
        ):
            _expect_value(control, key, expected, label)
        for key, expected in (
            ("agents_sha256", manifest["agents_sha256"]),
            ("trace_sha256", manifest["trace_sha256"]),
            ("place_assignments_sha256", manifest["place_assignments_sha256"]),
        ):
            _expect_value(pair, key, expected, label)
        response = control.get("response")
        if not isinstance(response, dict):
            _refuse(label, "study response arm receipt is missing")
        _exact_keys(
            response,
            {
                "response_input_sha256",
                "response_stream_sha256",
                "response_state_sha256",
                "response_summary_sha256",
                "response_stream_bytes",
                "response_count",
                "state_update_count",
                "response_campaign_count",
                "final_state_count",
                "response_assumption_structure_sha256",
                "response_metrics_sha256",
                "campaign_ids",
            },
            label,
        )
        for key in (
            "response_input_sha256",
            "response_stream_sha256",
            "response_state_sha256",
            "response_summary_sha256",
            "response_stream_bytes",
            "response_count",
            "state_update_count",
            "response_campaign_count",
            "final_state_count",
        ):
            _expect_value(response, key, manifest[key], label)
        for key, expected in (
            (
                "response_assumption_structure_sha256",
                direct_metrics["response_assumption_structure_sha256"],
            ),
            ("response_metrics_sha256", direct_hash),
            ("campaign_ids", list(campaign_ids)),
        ):
            _expect_value(response, key, expected, label)

        scalars = pair.get("scalars")
        if not isinstance(scalars, list) or len(scalars) != len(expected_keys):
            _refuse(label, "pair does not contain the complete scalar evidence shape")
        observations: dict[str, dict[str, object]] = {}
        for expected_key, scalar_value in zip(expected_keys, scalars, strict=True):
            if not isinstance(scalar_value, dict):
                _refuse(label, "scalar receipt is not a JSON object")
            _exact_keys(scalar_value, {"control", "treatment", "delta"}, label)
            control_scalar = scalar_value.get("control")
            if (
                not isinstance(control_scalar, dict)
                or scalar_value.get("treatment") != control_scalar
            ):
                _refuse(label, "A/A scalar arm receipts are not exactly identical")
            delta = scalar_value.get("delta")
            if type(delta) is not float or delta != 0.0 or math.copysign(1.0, delta) < 0.0:
                _refuse(label, "A/A scalar delta is not exact positive zero")
            observations[expected_key] = _verify_observation(
                control_scalar,
                key=expected_key,
                sources=expected_sources[expected_key],
                label=label,
            )
        attention_metrics = _attention_metrics_from_observations(
            document, pair, control, observations, label=label
        )
        _expect_value(
            control,
            "attention_metrics_sha256",
            sha256(_canonical_json_bytes(attention_metrics, label)).hexdigest(),
            label,
        )
        reconstructed_response = _response_metrics_from_observations(
            document, pair, control, observations, label=label
        )
        _verify_notice_response_alignment(attention_metrics, reconstructed_response, label)
        if reconstructed_response != direct_metrics:
            _refuse(label, "study scalar receipts do not reconstruct direct response metrics")

    statistics = document.get("statistics")
    if not isinstance(statistics, list) or len(statistics) != len(expected_keys):
        _refuse(label, "study does not contain one statistic per scalar observation")
    for expected_key, statistic_value in zip(expected_keys, statistics, strict=True):
        if not isinstance(statistic_value, dict):
            _refuse(label, "statistic is not a JSON object")
        statistic = statistic_value
        _exact_keys(
            statistic,
            {
                "schema_version",
                "metric_key",
                "source_artifacts",
                "n_seeds",
                "mean_paired_difference",
                "sample_standard_deviation",
                "median_paired_difference",
                "bootstrap_ci_low",
                "bootstrap_ci_high",
                "paired_standardized_difference",
                "positive_count",
                "negative_count",
                "zero_count",
                "agreement_fraction",
                "direction",
            },
            label,
        )
        _expect_value(statistic, "schema_version", 1, label)
        _expect_value(statistic, "metric_key", expected_key, label)
        _expect_value(statistic, "source_artifacts", expected_sources[expected_key], label)
        for field in (
            "mean_paired_difference",
            "sample_standard_deviation",
            "median_paired_difference",
            "bootstrap_ci_low",
            "bootstrap_ci_high",
        ):
            value = statistic.get(field)
            if type(value) is not float or value != 0.0 or math.copysign(1.0, value) < 0.0:
                _refuse(label, f"{field} is not exact positive zero")
        for key_name, expected in (
            ("n_seeds", 2),
            ("paired_standardized_difference", None),
            ("positive_count", 0),
            ("negative_count", 0),
            ("zero_count", 2),
            ("agreement_fraction", 1.0),
            ("direction", "stable-null"),
        ):
            _expect_value(statistic, key_name, expected, label)
    return sha256(_canonical_json_bytes(document, label)).hexdigest()


class _SpatialReportDocument(HTMLParser):
    """Collect security structure plus the report's public semantic evidence rows."""

    def __init__(self, content: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.element_paths: list[tuple[str, ...]] = []
        self.styles: list[str] = []
        self.scope_texts: list[str] = []
        self.scope_visibility: list[bool] = []
        self.section_ids: list[str] = []
        self.section_paths: list[tuple[str, ...]] = []
        self.section_terms: dict[str, list[tuple[str, str]]] = {}
        self.section_parts: dict[str, list[str]] = {}
        self.statistic_rows: list[list[str]] = []
        self.pair_ids: list[str] = []
        self.pair_texts: dict[str, str] = {}
        self.pair_terms: dict[str, list[tuple[str, str]]] = {}
        self.headings: list[tuple[str, dict[str, str | None], str, tuple[str, ...]]] = []
        self._style_parts: list[str] | None = None
        self._scope_parts: list[str] | None = None
        self._statistics_table_depth: int | None = None
        self._statistics_body_depth: int | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._pair_id: str | None = None
        self._pair_parts: list[str] | None = None
        self._term_parts: list[str] | None = None
        self._pending_term: str | None = None
        self._definitions: list[dict[str, object]] = []
        self._section_id: str | None = None
        self._heading: tuple[str, dict[str, str | None], tuple[str, ...]] | None = None
        self._heading_parts: list[str] | None = None
        self._stack: list[str] = []
        self._attribute_stack: list[dict[str, str | None]] = []
        self.feed(content)
        self.close()
        if (
            self._stack
            or self._attribute_stack
            or self._definitions
            or self._style_parts is not None
            or self._scope_parts is not None
            or self._statistics_table_depth is not None
            or self._statistics_body_depth is not None
            or self._row is not None
            or self._cell_parts is not None
            or self._pair_id is not None
            or self._pair_parts is not None
            or self._term_parts is not None
            or self._pending_term is not None
            or self._section_id is not None
            or self._heading is not None
            or self._heading_parts is not None
        ):
            raise ValueError("HTML document has unclosed semantic elements")

    @staticmethod
    def _text(parts: list[str]) -> str:
        return " ".join(" ".join(parts).split())

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attribute_names = [name for name, _value in attrs]
        if len(attribute_names) != len(set(attribute_names)):
            raise ValueError("duplicate HTML attribute")
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        path = (*self._stack, tag)
        self.element_paths.append(path)
        void_elements = {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
        if tag not in void_elements:
            self._stack.append(tag)
            self._attribute_stack.append(attributes)
        depth = len(self._stack)
        if tag == "style":
            if self._style_parts is not None:
                raise ValueError("nested style element")
            self._style_parts = []
        if tag in {"h1", "h2", "h3"}:
            if self._heading is not None:
                raise ValueError("nested report heading")
            self._heading = (tag, attributes, path)
            self._heading_parts = []
        classes = set((attributes.get("class") or "").split())
        if tag == "p" and "scope" in classes:
            if self._scope_parts is not None:
                raise ValueError("nested scope disclosure")
            self._scope_parts = []
            hidden_ancestor = any(
                "hidden" in item
                or "inert" in item
                or (item.get("aria-hidden") or "").casefold() == "true"
                for item in self._attribute_stack[:-1]
            )
            self.scope_visibility.append(
                attributes == {"class": "scope"}
                and not hidden_ancestor
                and path == ("html", "body", "header", "p")
            )
        if tag == "section":
            section_id = attributes.get("aria-labelledby")
            if (
                self._section_id is not None
                or not isinstance(section_id, str)
                or attributes != {"aria-labelledby": section_id}
            ):
                raise ValueError("invalid nested report section")
            self._section_id = section_id
            self.section_ids.append(section_id)
            self.section_paths.append(path)
            self.section_terms[section_id] = []
            self.section_parts[section_id] = []
        if tag == "table" and "statistics" in classes:
            if self._statistics_table_depth is not None:
                raise ValueError("nested statistics table")
            self._statistics_table_depth = depth
        elif tag == "tbody" and self._statistics_table_depth is not None:
            self._statistics_body_depth = depth
        elif tag == "tr" and self._statistics_body_depth is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell_parts = []
        if tag == "article" and "pair" in classes:
            pair_id = attributes.get("aria-labelledby")
            if (
                not isinstance(pair_id, str)
                or self._pair_id is not None
                or attributes != {"class": "pair", "aria-labelledby": pair_id}
            ):
                raise ValueError("invalid pair article")
            self._pair_id = pair_id
            self.pair_ids.append(pair_id)
            self._pair_parts = []
            self.pair_terms[pair_id] = []
        if tag == "dt":
            if (
                len(path) < 2
                or path[-2] != "dl"
                or self._term_parts is not None
                or self._pending_term is not None
            ):
                raise ValueError("invalid definition term sequence")
            self._term_parts = []
        elif tag == "dd":
            if len(path) < 2 or path[-2] != "dl" or self._pending_term is None:
                raise ValueError("definition value has no term")
            self._definitions.append(
                {
                    "term": self._pending_term,
                    "parts": [],
                    "nested": False,
                    "pair_id": self._pair_id,
                    "section_id": self._section_id,
                }
            )
            self._pending_term = None
        elif tag == "dl":
            for capture in self._definitions:
                capture["nested"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._style_parts is not None:
            self.styles.append("".join(self._style_parts))
            self._style_parts = None
        if tag in {"th", "td"} and self._cell_parts is not None:
            if self._row is None:
                raise ValueError("statistics cell is outside a row")
            self._row.append(self._text(self._cell_parts))
            self._cell_parts = None
        if tag == "tr" and self._row is not None:
            self.statistic_rows.append(self._row)
            self._row = None
        if tag == "tbody" and self._statistics_body_depth == len(self._stack):
            self._statistics_body_depth = None
        if tag == "table" and self._statistics_table_depth == len(self._stack):
            self._statistics_table_depth = None
        if tag == "p" and self._scope_parts is not None:
            self.scope_texts.append(self._text(self._scope_parts))
            self._scope_parts = None
        if tag in {"h1", "h2", "h3"} and self._heading is not None:
            if self._heading_parts is None or self._heading[0] != tag:
                raise ValueError("report heading capture is invalid")
            heading_tag, attributes, path = self._heading
            self.headings.append((heading_tag, attributes, self._text(self._heading_parts), path))
            self._heading = None
            self._heading_parts = None
        if tag == "dt" and self._term_parts is not None:
            self._pending_term = self._text(self._term_parts)
            self._term_parts = None
        if tag == "dd":
            if not self._definitions:
                raise ValueError("definition value stack is empty")
            capture = self._definitions.pop()
            rendered = "<nested-ledger>" if capture["nested"] else None
            if isinstance(capture["pair_id"], str):
                parts = capture["parts"]
                if not isinstance(parts, list) or any(not isinstance(item, str) for item in parts):
                    raise ValueError("definition value capture is invalid")
                self.pair_terms[capture["pair_id"]].append(
                    (str(capture["term"]), rendered or self._text(parts))
                )
            elif isinstance(capture["section_id"], str):
                parts = capture["parts"]
                if not isinstance(parts, list) or any(not isinstance(item, str) for item in parts):
                    raise ValueError("section definition value capture is invalid")
                self.section_terms[capture["section_id"]].append(
                    (str(capture["term"]), rendered or self._text(parts))
                )
        if tag == "article" and self._pair_id is not None:
            if self._pair_parts is None:
                raise ValueError("pair article capture is invalid")
            self.pair_texts[self._pair_id] = self._text(self._pair_parts)
            self._pair_id = None
            self._pair_parts = None
        if tag == "section":
            self._section_id = None
        if not self._stack or self._stack[-1] != tag:
            raise ValueError("HTML element nesting is invalid")
        self._stack.pop()
        self._attribute_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._style_parts is not None:
            self._style_parts.append(data)
            return
        if self._scope_parts is not None:
            self._scope_parts.append(data)
        if self._section_id is not None:
            self.section_parts[self._section_id].append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)
        if self._pair_parts is not None:
            self._pair_parts.append(data)
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        if self._term_parts is not None:
            self._term_parts.append(data)
        for capture in self._definitions:
            parts = capture["parts"]
            if not isinstance(parts, list):
                raise ValueError("definition capture is invalid")
            parts.append(data)


_SPATIAL_REPORT_STYLE_SHA256 = "q6ztJ+LuDuNfjt0kfSzFQsUp4JljF3XBszCmp4i5h2o="


def _contained_real_directory(root: Path, name: str, label: str) -> Path:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    try:
        root_status = root.lstat()
        resolved_root = root.resolve(strict=True)
        directory = root / name
        directory_status = directory.lstat()
        resolved_directory = directory.resolve(strict=True)
    except OSError:
        _refuse(label, "report directory is missing or unsafe")
    if (
        not stat.S_ISDIR(root_status.st_mode)
        or stat.S_ISLNK(root_status.st_mode)
        or getattr(root_status, "st_file_attributes", 0) & reparse_flag
        or not stat.S_ISDIR(directory_status.st_mode)
        or stat.S_ISLNK(directory_status.st_mode)
        or getattr(directory_status, "st_file_attributes", 0) & reparse_flag
        or resolved_directory != resolved_root / name
    ):
        _refuse(label, "report directory is not one real contained directory")
    return directory


def _rendered_leaf_terms(value: dict[str, object]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for key, item in value.items():
        if key == "scalars":
            continue
        if isinstance(item, dict):
            terms.extend(_rendered_leaf_terms(item))
            terms.append((key, "<nested-ledger>"))
        elif isinstance(item, list):
            rendered = " ".join("null" if entry is None else str(entry) for entry in item)
            terms.append((key, rendered))
        else:
            terms.append((key, "null" if item is None else str(item)))
    return terms


def _verify_report_semantics(
    document: _SpatialReportDocument, study: dict[str, object], label: str
) -> None:
    if len(document.scope_texts) != 1 or document.scope_visibility != [True]:
        _refuse(label, "report lacks one prominent claim-scope disclosure")
    disclosure = document.scope_texts[0].casefold()
    if any(
        phrase not in disclosure
        for phrase in ("synthetic", "exploratory", "unobserved", "non-causal", "not sales")
    ):
        _refuse(label, "report's prominent disclosure is scientifically incomplete")
    expected_sections = (
        "evidence-route-heading",
        "protocol-heading",
        "city-heading",
        "statistics-heading",
        "pairs-heading",
        "assumptions-heading",
        "reproduction-heading",
        "limitations-heading",
    )
    if tuple(document.section_ids) != expected_sections or any(
        path != ("html", "body", "main", "section") for path in document.section_paths
    ):
        _refuse(label, "report does not contain its exact ordered public sections")
    expected_headings = [
        ("h1", {}, "Study verdict", ("html", "body", "header", "h1")),
        *[
            (
                "h2",
                {"id": heading_id},
                text,
                ("html", "body", "main", "section", "h2"),
            )
            for heading_id, text in (
                ("evidence-route-heading", "Evidence route"),
                ("protocol-heading", "Study and protocol receipts"),
                ("city-heading", "City provenance"),
                ("statistics-heading", "Aggregate statistics"),
                ("pairs-heading", "Pair provenance ledger"),
            )
        ],
        *[
            (
                "h3",
                {"id": f"pair-{seed}"},
                f"Seed {seed} · control / treatment receipts",
                ("html", "body", "main", "section", "article", "h3"),
            )
            for seed in range(2)
        ],
        (
            "h2",
            {"id": "assumptions-heading"},
            "Assumptions",
            ("html", "body", "main", "section", "h2"),
        ),
        (
            "h3",
            {},
            "Transparent response and campaign state rules",
            ("html", "body", "main", "section", "h3"),
        ),
        (
            "h2",
            {"id": "reproduction-heading"},
            "Reproduction",
            ("html", "body", "main", "section", "h2"),
        ),
        (
            "h2",
            {"id": "limitations-heading"},
            "Limitations",
            ("html", "body", "main", "section", "h2"),
        ),
    ]
    if document.headings != expected_headings:
        _refuse(label, "report heading identities and visible meanings are not exact")

    protocol = {
        key: value
        for key, value in study.items()
        if key not in {"city", "definition", "pairs", "statistics"}
    }
    expected_protocol_terms = _rendered_leaf_terms(protocol)
    expected_protocol_terms.append(
        ("study_result_sha256", sha256(_canonical_json_bytes(study, label)).hexdigest())
    )
    if document.section_terms["protocol-heading"] != expected_protocol_terms:
        _refuse(label, "report protocol ledger is not the complete study receipt")
    city = study.get("city")
    if not isinstance(city, dict) or document.section_terms["city-heading"] != (
        _rendered_leaf_terms(city)
    ):
        _refuse(label, "report city ledger is not the complete public provenance")
    definition = study.get("definition")
    if not isinstance(definition, dict):
        _refuse(label, "study definition is invalid")
    reproduction = _SpatialReportDocument._text(document.section_parts["reproduction-heading"])
    for evidence in (
        "adlife --format json city-study ROOT STUDY.json",
        "adlife --format json city-report ROOT STUDY.json",
        _canonical_json_bytes(definition, label).decode("utf-8"),
    ):
        if evidence not in reproduction:
            _refuse(label, "report reproduction section omits canonical evidence")
    assumptions = _SpatialReportDocument._text(document.section_parts["assumptions-heading"])
    limitations = _SpatialReportDocument._text(document.section_parts["limitations-heading"])
    if any(
        str(study.get(key)) not in assumptions
        for key in ("opportunity_model_id", "attention_model_id", "response_model_id")
    ) or any(
        phrase not in limitations.casefold()
        for phrase in ("synthetic", "unobserved", "non-causal", "not sales", "external validity")
    ):
        _refuse(label, "report assumptions or limitations are scientifically incomplete")
    statistics = study.get("statistics")
    if not isinstance(statistics, list) or len(document.statistic_rows) != len(statistics):
        _refuse(label, "report does not render every aggregate statistic")
    for statistic, row in zip(statistics, document.statistic_rows, strict=True):
        if not isinstance(statistic, dict) or len(row) != 7:
            _refuse(label, "report statistic row has an invalid semantic shape")
        sources = statistic.get("source_artifacts")
        if not isinstance(sources, list):
            _refuse(label, "study statistic sources are invalid")
        expected_identity = " ".join(
            (str(statistic.get("metric_key")), *(str(source) for source in sources))
        )
        if row[0] != expected_identity:
            _refuse(label, "report statistic row omits its identity or source provenance")
        standardized = statistic.get("paired_standardized_difference")
        expected_cells = (
            str(statistic.get("n_seeds")),
            (
                f"Mean: {statistic.get('mean_paired_difference')} "
                f"Median: {statistic.get('median_paired_difference')}"
            ),
            (
                f"Sample SD: {statistic.get('sample_standard_deviation')} "
                "Standardized difference: "
                f"{'null' if standardized is None else standardized}"
            ),
            (
                f"Low: {statistic.get('bootstrap_ci_low')} "
                f"High: {statistic.get('bootstrap_ci_high')}"
            ),
            (
                f"Positive: {statistic.get('positive_count')} "
                f"Negative: {statistic.get('negative_count')} "
                f"Zero: {statistic.get('zero_count')}"
            ),
            f"{statistic.get('direction')} Agreement: {statistic.get('agreement_fraction')}",
        )
        if tuple(row[1:]) != expected_cells:
            _refuse(label, "report statistic row changes an aggregate receipt")
    pairs = study.get("pairs")
    if not isinstance(pairs, list):
        _refuse(label, "study pair evidence is invalid")
    expected_pair_ids = [f"pair-{seed}" for seed in range(len(pairs))]
    if (
        document.pair_ids != expected_pair_ids
        or list(document.pair_texts) != expected_pair_ids
        or set(document.pair_terms) != set(expected_pair_ids)
    ):
        _refuse(label, "report does not contain every canonical pair provenance article")
    for pair_id, pair in zip(expected_pair_ids, pairs, strict=True):
        if not isinstance(pair, dict):
            _refuse(label, "study pair receipt is invalid")
        actual = list(document.pair_terms[pair_id])
        for expected in _rendered_leaf_terms(pair):
            try:
                actual.remove(expected)
            except ValueError:
                _refuse(label, "report pair ledger omits or changes provenance")
        if actual:
            _refuse(label, "report pair ledger contains provenance absent from the study")


def _verify_spatial_report(
    root: Path,
    receipt: dict[str, object],
    *,
    study_definition_sha256: str,
    study_result_sha256: str,
    study: dict[str, object],
) -> bytes:
    """Verify the exact static installed-wheel report and its nine-field receipt."""
    label = "city-report"
    expected_identity: dict[str, object] = {
        "schema_version": 1,
        "format_id": "spatial-study-html-v1",
        "claim_scope": "synthetic-study-not-observed-or-causal-effect",
        "study_id": "wheel-aa-study",
        "study_definition_sha256": study_definition_sha256,
        "study_result_sha256": study_result_sha256,
        "report_path": "city-reports/wheel-aa-study.html",
    }
    if set(receipt) != {*expected_identity, "report_sha256", "report_bytes"}:
        _refuse(label, "receipt does not contain exactly the nine public fields")
    for key, expected in expected_identity.items():
        _expect_value(receipt, key, expected, label)
    for key in ("study_definition_sha256", "study_result_sha256", "report_sha256"):
        _expect_sha256(receipt, key, label)
    _expect_value(study, "study_definition_sha256", study_definition_sha256, label)
    if sha256(_canonical_json_bytes(study, label)).hexdigest() != study_result_sha256:
        _refuse(label, "report result digest is not the exact study document")

    report_directory = _contained_real_directory(root, "city-reports", label)
    report = report_directory / "wheel-aa-study.html"
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    try:
        report_status = report.lstat()
    except OSError:
        _refuse(label, "fixed report destination is missing or unsafe")
    if (
        not stat.S_ISREG(report_status.st_mode)
        or stat.S_ISLNK(report_status.st_mode)
        or getattr(report_status, "st_file_attributes", 0) & reparse_flag
        or report.resolve(strict=True).parent != report_directory.resolve(strict=True)
    ):
        _refuse(label, "fixed report destination is missing or unsafe")
    content = _regular_file_bytes(report, label, maximum=8_388_608)
    _expect_value(receipt, "report_bytes", len(content), label)
    _expect_value(receipt, "report_sha256", sha256(content).hexdigest(), label)
    if not content or len(content) > 8_388_608:
        _refuse(label, "report is outside the supported byte bound")
    if b"\r" in content or not content.endswith(b"\n"):
        _refuse(label, "report is not normalized LF-terminated UTF-8")
    try:
        text = content.decode("utf-8", errors="strict")
        document = _SpatialReportDocument(text)
    except (UnicodeDecodeError, ValueError) as error:
        _refuse(label, f"report is not valid inert UTF-8 HTML: {error}")

    allowed_elements = {
        "a",
        "article",
        "bdi",
        "body",
        "br",
        "caption",
        "code",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "h1",
        "h2",
        "h3",
        "head",
        "header",
        "html",
        "li",
        "main",
        "meta",
        "ol",
        "p",
        "pre",
        "section",
        "span",
        "strong",
        "style",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
    }
    tags = {tag for tag, _attrs in document.elements}
    if tags - allowed_elements:
        _refuse(label, "report contains an element outside the exact inert template vocabulary")

    resource_attributes = {
        "src",
        "srcset",
        "srcdoc",
        "action",
        "formaction",
        "poster",
        "background",
        "data",
        "ping",
        "xlink:href",
    }
    links: list[tuple[str, str | None]] = []
    for tag, attrs in document.elements:
        if (
            "hidden" in attrs
            or "inert" in attrs
            or "popover" in attrs
            or (attrs.get("aria-hidden") or "").casefold() == "true"
        ):
            _refuse(label, "report hides required semantic evidence")
        if "style" in attrs or any(name.startswith("on") for name in attrs):
            _refuse(label, "report contains an inline execution-capable attribute")
        if attrs.keys() & resource_attributes:
            _refuse(label, "report contains a resource-loading attribute")
        if "href" in attrs:
            links.append((tag, attrs["href"]))
    if links != [("a", "#main")]:
        _refuse(label, "report contains a link outside the one same-document skip link")
    indexed_elements = tuple(zip(document.elements, document.element_paths, strict=True))
    element_ids = [attrs["id"] for _tag, attrs in document.elements if "id" in attrs]
    if any(not isinstance(value, str) or not value for value in element_ids) or len(
        element_ids
    ) != len(set(element_ids)):
        _refuse(label, "report contains an invalid or duplicate element identity")
    if [
        (attrs, path)
        for (tag, attrs), path in indexed_elements
        if "skip-link" in (attrs.get("class") or "").split()
    ] != [({"class": "skip-link", "href": "#main"}, ("html", "body", "a"))]:
        _refuse(label, "report skip-link structure is not exact")
    if [(attrs, path) for (tag, attrs), path in indexed_elements if tag == "main"] != [
        ({"id": "main", "tabindex": "-1"}, ("html", "body", "main"))
    ]:
        _refuse(label, "report main evidence container is not exact")
    if [(attrs, path) for (tag, attrs), path in indexed_elements if tag == "header"] != [
        ({"class": "study-header"}, ("html", "body", "header"))
    ]:
        _refuse(label, "report disclosure header is not exact")
    if [(attrs, path) for (tag, attrs), path in indexed_elements if tag == "title"] != [
        ({}, ("html", "head", "title"))
    ]:
        _refuse(label, "report title is not one safe head element")
    if [
        (attrs, path)
        for (tag, attrs), path in indexed_elements
        if tag == "table" and "statistics" in (attrs.get("class") or "").split()
    ] != [({"class": "statistics"}, ("html", "body", "main", "section", "div", "table"))]:
        _refuse(label, "report statistics table structure is not exact")
    if len(document.styles) != 1:
        _refuse(label, "report does not contain exactly one inline stylesheet")

    css = document.styles[0]
    lowered_css = css.casefold()
    if any(marker in lowered_css for marker in ("url(", "@import", "@font-face")):
        _refuse(label, "report stylesheet contains a network-capable rule")
    style_digest = base64.b64encode(sha256(css.encode("utf-8")).digest()).decode("ascii")
    if style_digest != _SPATIAL_REPORT_STYLE_SHA256:
        _refuse(label, "report does not embed the exact packaged stylesheet")
    expected_csp = (
        "default-src 'none'; script-src 'none'; "
        f"style-src 'sha256-{style_digest}'; "
        "img-src 'none'; font-src 'none'; connect-src 'none'; media-src 'none'; "
        "object-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    http_equiv = [
        (index, tag, attrs.get("http-equiv"), attrs.get("content"))
        for index, (tag, attrs) in enumerate(document.elements)
        if "http-equiv" in attrs
    ]
    if [(tag, header, content) for _index, tag, header, content in http_equiv] != [
        ("meta", "Content-Security-Policy", expected_csp)
    ]:
        _refuse(label, "report CSP does not authorize exactly its inline stylesheet")
    style_indices = [
        index for index, (tag, _attrs) in enumerate(document.elements) if tag == "style"
    ]
    csp_index = http_equiv[0][0]
    if (
        len(style_indices) != 1
        or csp_index > style_indices[0]
        or document.element_paths[csp_index] != ("html", "head", "meta")
        or document.element_paths[style_indices[0]] != ("html", "head", "style")
    ):
        _refuse(label, "report CSP appears after its inline stylesheet")
    if "NaN" in text or "Infinity" in text:
        _refuse(label, "report contains a non-finite numeric token")
    _verify_report_semantics(document, study, label)
    forbidden = {
        str(root.resolve()),
        root.resolve().as_posix(),
        "person-001",
        "person-002",
        _FICTIONAL_CREATIVE_SHA256,
        "private-provider-key",
        "authorization: bearer",
        "ADLIFE_API_KEY",
        "raw_response",
        "raw-response",
        '"profiles"',
        '"traits"',
        '"interests"',
        '"target_interests"',
        '"initial_states"',
    }
    casefolded_report = text.casefold()
    marker_counts = {
        marker: len(re.findall(rf"(?<![\w-]){re.escape(marker)}(?![\w-])", casefolded_report))
        for marker in _REPORT_RESPONSE_MARKER_COUNTS
    }
    if any(marker and marker.casefold() in casefolded_report for marker in forbidden) or (
        marker_counts != _REPORT_RESPONSE_MARKER_COUNTS
    ):
        _refuse(label, "report exposes local, private, credential, or raw input material")
    return content


def _write_fictional_place_set(path: Path, *, city_sha256: str) -> None:
    """Create the clean-room fixture without reading from the source checkout."""
    provenance = {"method": "operator-authored-fictional"}
    document = {
        "schema_version": 1,
        "city_id": "fictional-grid-v2",
        "city_sha256": city_sha256,
        "name": "Fictional clean-room smoke places",
        "places": [
            {
                "place_id": "home-west",
                "kind": "home",
                "node_id": "west-north",
                "label": "Fictional west home",
                "provenance": provenance,
            },
            {
                "place_id": "home-east",
                "kind": "home",
                "node_id": "east-north",
                "label": "Fictional east home",
                "provenance": provenance,
            },
            {
                "place_id": "work-center",
                "kind": "workplace",
                "node_id": "center-center",
                "label": "Fictional center workplace",
                "provenance": provenance,
            },
            {
                "place_id": "leisure-south",
                "kind": "leisure",
                "node_id": "center-south",
                "label": "Fictional south leisure venue",
                "provenance": provenance,
            },
        ],
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_fictional_spatial_campaign(path: Path, *, city_sha256: str) -> None:
    """Create one explicit two-channel validation fixture for the packaged grid."""
    document = {
        "schema_version": 1,
        "scenario_id": "clean-room-spatial",
        "name": "Fictional clean-room spatial campaign",
        "days": 1,
        "city_id": "fictional-grid-v2",
        "city_sha256": city_sha256,
        "campaigns": [
            {
                "campaign_id": "fictional-launch",
                "name": "Fictional product launch",
                "creative_sha256": _FICTIONAL_CREATIVE_SHA256,
            }
        ],
        "placements": [
            {
                "placement_id": "north-road-billboard",
                "campaign_id": "fictional-launch",
                "channel": "roadside-billboard",
                "active_windows": [{"start_minute": 360, "end_minute": 1_140}],
                "frequency_cap_per_agent_per_day": 3,
                "road_id": "north-west",
                "travel_direction": "forward",
                "road_fraction": 0.5,
                "longitude": 0.02,
                "latitude": 0.06,
                "side": "right",
                "orientation_degrees": 180.0,
                "max_view_distance_meters": 120.0,
            },
            {
                "placement_id": "fictional-mobile-feed",
                "campaign_id": "fictional-launch",
                "channel": "mobile-feed",
                "active_windows": [{"start_minute": 360, "end_minute": 1_140}],
                "frequency_cap_per_agent_per_day": 2,
                "opportunity_model": "keyed-activity-minute-v1",
                "eligible_activities": ["home", "commute", "work", "leisure"],
                "opportunity_probability_per_minute": 0.05,
            },
        ],
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_fictional_spatial_response(
    path: Path,
    *,
    city_sha256: str,
    scenario_sha256: str,
) -> None:
    """Create explicit fictional response assumptions for the two-agent smoke cohort."""
    document = {
        "schema_version": 1,
        "city_sha256": city_sha256,
        "scenario_sha256": scenario_sha256,
        "profiles": [
            {
                "agent_id": "person-001",
                "fictional": True,
                "interests": ["coffee", "outdoors"],
                "traits": {
                    "price_sensitivity": 0.8,
                    "novelty_seeking": 0.3,
                    "advertising_skepticism": 0.6,
                    "mobile_recall_encoding": 0.7,
                    "roadside_recall_encoding": 0.4,
                    "impulsivity": 0.2,
                },
            },
            {
                "agent_id": "person-002",
                "fictional": True,
                "interests": ["coffee", "work"],
                "traits": {
                    "price_sensitivity": 0.2,
                    "novelty_seeking": 0.9,
                    "advertising_skepticism": 0.1,
                    "mobile_recall_encoding": 0.8,
                    "roadside_recall_encoding": 0.5,
                    "impulsivity": 0.7,
                },
            },
        ],
        "campaigns": [
            {
                "campaign_id": "fictional-launch",
                "creative_sha256": _FICTIONAL_CREATIVE_SHA256,
                "target_interests": ["coffee", "outdoors"],
                "relative_price": 1.0,
            }
        ],
        "initial_states": [
            {
                "agent_id": agent_id,
                "campaign_id": "fictional-launch",
                "brand_sentiment": 0.0,
                "recall_strength": 0.0,
                "purchase_intention": 0.0,
            }
            for agent_id in ("person-001", "person-002")
        ],
    }
    path.write_bytes(
        (
            json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    )


def _write_spatial_aa_study(path: Path) -> None:
    """Create the exact two-seed installed-wheel A/A study definition."""
    document = {
        "schema_version": 1,
        "study_id": "wheel-aa-study",
        "design": "a-a",
        "analysis_scope": "attention-and-response",
        "pairs": [
            {
                "seed": seed,
                "control_run_id": f"catalog-seed-{seed}",
                "treatment_run_id": f"catalog-seed-{seed}",
            }
            for seed in (0, 1)
        ],
    }
    path.write_bytes(
        (
            json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    )


def _uv_executable() -> str | None:
    found = shutil.which("uv")
    return str(Path(found).resolve()) if found else None


def _create_interpreter(workspace: Path) -> tuple[Path, str | None]:
    """Create a venv with a supported interpreter; prefer uv when available.

    Reuse the executing Python when supported so CI tests its selected Python
    version and offline checks need no additional interpreter download. For an
    unsupported host interpreter, uv selects 3.13. uv-created venvs carry no pip,
    so installation goes through ``uv pip install --python`` in that case.
    """
    venv = workspace / "venv"
    uv = _uv_executable()
    if uv is not None:
        interpreter = sys.executable if (3, 11) <= sys.version_info[:2] < (3, 14) else "3.13"
        _run([uv, "venv", "--python", interpreter, str(venv)])
        scripts = "Scripts" if sys.platform == "win32" else "bin"
        return (
            venv / scripts / "python.exe" if sys.platform == "win32" else venv / scripts / "python",
            uv,
        )
    _run([sys.executable, "-m", "venv", str(venv)])
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    return (
        venv / scripts / "python.exe" if sys.platform == "win32" else venv / scripts / "python",
        None,
    )


def _install_wheel(python: Path, uv: str | None, wheel: Path, *, no_deps: bool = False) -> None:
    dependency_option = ["--no-deps"] if no_deps else []
    if uv is not None:
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--quiet",
                *dependency_option,
                str(wheel),
            ],
            allow_network_environment=not no_deps,
        )
    else:
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                *dependency_option,
                str(wheel),
            ],
            allow_network_environment=not no_deps,
        )


def _child_site_packages(python: Path, *, cwd: Path | None = None) -> Path:
    completed = _run(
        [
            str(python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        cwd=cwd,
    )
    child_site = Path(completed.stdout.strip()).resolve()
    environment_root = python.parent.parent.resolve()
    if not child_site.is_relative_to(environment_root):
        raise RuntimeError("child site-packages is outside the smoke environment")
    if not child_site.is_dir() or child_site.is_symlink():
        raise RuntimeError("child site-packages does not exist or is a symlink")
    return child_site


def _install_network_guard(python: Path, *, cwd: Path | None = None) -> Path:
    """Install a temporary interpreter-wide deny hook into the fresh smoke venv."""
    child_site = _child_site_packages(python, cwd=cwd)
    guard = child_site / "sitecustomize.py"
    try:
        with guard.open("x", encoding="utf-8", newline="\n") as target:
            target.write(_NETWORK_GUARD_SOURCE)
    except FileExistsError:
        raise RuntimeError("refusing to replace a pre-existing smoke sitecustomize.py") from None
    if guard.is_symlink() or guard.read_text(encoding="utf-8") != _NETWORK_GUARD_SOURCE:
        raise RuntimeError("failed to verify the smoke network guard")
    return guard


def _verify_network_guard_log(
    guard: Path,
    *,
    adlife: Path,
    viewer_probe: Path,
    required_tokens: tuple[str, ...] = (),
    viewer_token: str | None = None,
) -> None:
    """Prove launchers and each named certification child loaded sitecustomize."""
    log = guard.with_name(_NETWORK_GUARD_LOG)
    if not log.is_file() or log.is_symlink() or log.stat().st_size > 64 * 1024:
        raise RuntimeError("smoke network guard activation log is missing or unsafe")
    entries: list[tuple[str, str]] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        token, separator, executable = line.partition("\t")
        if not separator or not executable:
            raise RuntimeError("smoke network guard activation log is malformed")
        if not token:
            raise RuntimeError("smoke network guard child record has no invocation token")
        entries.append((token, executable))
    launcher_names = {adlife.name.casefold(), "adlife", "adlife.exe"}
    if not any(
        Path(executable).name.casefold() in launcher_names for _token, executable in entries
    ):
        raise RuntimeError("installed adlife launcher did not load the smoke network guard")
    if not any(
        Path(executable).resolve() == viewer_probe.resolve() for _token, executable in entries
    ):
        raise RuntimeError("isolated viewer probe did not load the smoke network guard")
    recorded_tokens = tuple(token for token, _executable in entries)
    if any(recorded_tokens.count(token) != 1 for token in required_tokens):
        raise RuntimeError("a required smoke subprocess lacks unique network-guard evidence")
    if set(recorded_tokens) != set(required_tokens):
        raise RuntimeError("smoke network guard recorded an undeclared child invocation")
    for token in required_tokens:
        executable = next(value for recorded, value in entries if recorded == token)
        if token == viewer_token:
            matches = Path(executable).resolve() == viewer_probe.resolve()
        else:
            matches = Path(executable).name.casefold() in launcher_names
        if not matches:
            raise RuntimeError("smoke network guard token was recorded by the wrong child")


def _verify_installed_viewer(
    python: Path,
    *,
    root: Path,
    run_id: str,
    child_site: Path,
    response_count: int,
    final_state_count: int,
    cwd: Path,
) -> Path:
    """Probe the installed ASGI app in-process without starting an HTTP server."""
    probe = cwd / "verify-installed-city-viewer.py"
    try:
        with probe.open("x", encoding="utf-8", newline="\n") as target:
            target.write(_VIEWER_PROBE_SOURCE)
    except FileExistsError:
        raise RuntimeError("refusing to replace a pre-existing viewer probe") from None
    if probe.is_symlink() or probe.read_text(encoding="utf-8") != _VIEWER_PROBE_SOURCE:
        raise RuntimeError("failed to verify the installed viewer probe")
    completed = _run(
        [str(python), "-I", str(probe), str(root), run_id, str(child_site)],
        cwd=cwd,
        network_guard_token=_VIEWER_GUARD_TOKEN,
    )
    receipt = _expect_json(completed, "installed city viewer")
    for key, expected in (
        ("run_schema_version", 6),
        ("response_model_id", "spatial-response-v1"),
        ("claim_scope", "synthetic-response-not-observed-behavior"),
        ("summary_model_id", "spatial-response-artifact-v1"),
        ("state_model_id", "spatial-response-state-v1"),
        ("state_scope", "final-end-of-run-not-scrubbed-minute"),
        ("response_count", response_count),
        ("final_state_count", final_state_count),
        ("child_site_path", str(child_site.resolve())),
        ("module_origin_verified", True),
        ("network_guard_verified", True),
        ("ui_verified", True),
    ):
        _expect_value(receipt, key, expected, "installed city viewer")
    module_path = receipt.get("adlife_module_path")
    if not isinstance(module_path, str) or not Path(module_path).resolve().is_relative_to(
        child_site.resolve()
    ):
        _refuse("installed city viewer", "adlife was not imported from the child environment")
    return probe


def _expose_locked_dependencies(python: Path) -> None:
    """Expose this locked environment's dependencies to an isolated wheel venv.

    This is solely for the repository's network-free pytest smoke. The wheel is still
    installed into a fresh environment and is not imported from the source checkout.
    Release CI deliberately omits this mode and resolves a genuinely clean environment.
    """
    child_site = _child_site_packages(python)

    host_sites = sorted(
        {
            path
            for value in site.getsitepackages()
            if (path := Path(value).resolve()).is_dir() and path != child_site
        },
        key=str,
    )
    if not host_sites:
        raise RuntimeError("locked host dependencies are unavailable")
    dependency_paths = "".join(f"{path}\n" for path in host_sites)
    (child_site / "adlife-smoke-locked-dependencies.pth").write_text(
        dependency_paths, encoding="utf-8"
    )


def _certify_spatial_study(
    adlife: Path,
    *,
    scratch: Path,
    places_path: Path,
    spatial_path: Path,
    response_path: Path,
    catalog_sha256: str,
    place_set_sha256: str | None,
    scenario_sha256: str,
    response_input_sha256: str,
) -> int:
    """Certify installed schema-v6 response analysis and no-clobber reporting offline."""
    root = scratch / "city-output"
    run_directories: dict[int, Path] = {}
    for seed in (0, 1):
        run_id = f"catalog-seed-{seed}"
        run_document = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-run",
                    "--city-id",
                    "fictional-grid-v2",
                    "--places",
                    str(places_path),
                    "--spatial-campaign",
                    str(spatial_path),
                    "--spatial-response",
                    str(response_path),
                    "--output-root",
                    "city-output",
                    "--run-id",
                    run_id,
                    "--agents",
                    "2",
                    "--days",
                    "1",
                    "--seed",
                    str(seed),
                ],
                cwd=scratch,
                network_guard_token=f"spatial-run-{seed}",
            ),
            f"city-run seed {seed}",
        )
        for key, expected in (
            ("run_id", run_id),
            ("run_schema_version", 6),
            ("city_id", "fictional-grid-v2"),
            ("city_sha256", catalog_sha256),
            ("place_set_sha256", place_set_sha256),
            ("scenario_sha256", scenario_sha256),
            ("response_model_id", "spatial-response-v1"),
            ("response_input_sha256", response_input_sha256),
            ("response_campaign_count", 1),
            ("final_state_count", 2),
        ):
            _expect_value(run_document, key, expected, f"city-run seed {seed}")
        for key in ("response_stream_sha256", "response_state_sha256"):
            _expect_sha256(run_document, key, f"city-run seed {seed}")
        directory = root / "city-runs" / run_id
        manifest, _manifest_sha256 = _canonical_manifest_document(
            directory, f"city-run seed {seed}"
        )
        _verify_v6_manifest(manifest, f"city-run seed {seed}")
        _verify_city_run_receipt(
            run_document,
            manifest,
            directory=directory,
            label=f"city-run seed {seed}",
        )
        if place_set_sha256 is not None:
            _expect_value(manifest, "place_schema_version", 1, f"city-run seed {seed}")
            _expect_value(
                manifest,
                "place_set_sha256",
                place_set_sha256,
                f"city-run seed {seed}",
            )
            _expect_sha256(manifest, "place_assignments_sha256", f"city-run seed {seed}")
        run_directories[seed] = directory

    snapshots = {seed: _artifact_hashes(directory) for seed, directory in run_directories.items()}

    def expect_sources_unchanged(boundary: str) -> None:
        for seed, directory in run_directories.items():
            _expect_artifacts_unchanged(directory, snapshots[seed], boundary)

    response_metrics: dict[int, dict[str, object]] = {}
    response_metrics_sha256: dict[int, str] = {}
    for seed in (0, 1):
        run_id = f"catalog-seed-{seed}"
        metrics = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-metrics",
                    "city-output",
                    run_id,
                    "--layer",
                    "response",
                ],
                cwd=scratch,
                network_guard_token=f"spatial-metrics-{seed}",
            ),
            f"city-metrics response seed {seed}",
        )
        response_metrics[seed] = metrics
        response_metrics_sha256[seed] = _verify_response_metrics(
            metrics,
            run_directory=run_directories[seed],
            expected_campaign_ids=("fictional-launch",),
        )
        expect_sources_unchanged(f"city-metrics response seed {seed}")

    study_path = scratch / "wheel-aa-study.json"
    _write_spatial_aa_study(study_path)
    definition_bytes = study_path.read_bytes()
    study = _expect_json(
        _run(
            [
                str(adlife),
                "--format",
                "json",
                "city-study",
                "city-output",
                str(study_path),
            ],
            cwd=scratch,
            network_guard_token="spatial-study",
        ),
        "city-study A/A",
    )
    study_result_sha256 = _verify_spatial_aa_study(
        study,
        definition_bytes=definition_bytes,
        run_directories=run_directories,
        response_metrics=response_metrics,
        response_metrics_sha256=response_metrics_sha256,
        expected_place_set_sha256=place_set_sha256,
    )
    study_definition_sha256 = sha256(definition_bytes[:-1]).hexdigest()
    expect_sources_unchanged("city-study A/A")

    report_command = [
        str(adlife),
        "--format",
        "json",
        "city-report",
        "city-output",
        str(study_path),
    ]
    receipt = _expect_json(
        _run(report_command, cwd=scratch, network_guard_token="spatial-report"),
        "city-report",
    )
    report_bytes = _verify_spatial_report(
        root,
        receipt,
        study_definition_sha256=study_definition_sha256,
        study_result_sha256=study_result_sha256,
        study=study,
    )
    expect_sources_unchanged("city-report")

    report_directory = _contained_real_directory(root, "city-reports", "city-report")
    report_entries = tuple(sorted(path.name for path in report_directory.iterdir()))
    if report_entries != ("wheel-aa-study.html",):
        _refuse("city-report", "report directory contains an unexpected entry")
    conflict = _run(
        report_command,
        cwd=scratch,
        expected_exit=3,
        network_guard_token="spatial-report-conflict",
    )
    _expect_error_json(
        conflict,
        "city-report conflict",
        exit_code=3,
        message="report destination already exists",
    )
    post_conflict_bytes = _verify_spatial_report(
        root,
        receipt,
        study_definition_sha256=study_definition_sha256,
        study_result_sha256=study_result_sha256,
        study=study,
    )
    if post_conflict_bytes != report_bytes:
        _refuse("city-report conflict", "existing report bytes changed after refusal")
    report_directory = _contained_real_directory(root, "city-reports", "city-report conflict")
    if tuple(sorted(path.name for path in report_directory.iterdir())) != report_entries:
        _refuse("city-report conflict", "report conflict leaked a temporary directory entry")
    expect_sources_unchanged("city-report conflict")
    return len(report_bytes)


def smoke(wheel: Path, *, reuse_locked_dependencies: bool = False) -> None:
    """Verify the wheel, then remove all temporary artifacts before returning."""
    workspace = Path(tempfile.mkdtemp(prefix="adlife-smoke-"))
    try:
        scratch = workspace / "scratch"
        scratch.mkdir(parents=True)
        python, uv = _create_interpreter(workspace)
        adlife = python.parent / ("adlife.exe" if sys.platform == "win32" else "adlife")

        if reuse_locked_dependencies:
            _expose_locked_dependencies(python)
        _install_wheel(python, uv, wheel, no_deps=reuse_locked_dependencies)
        network_guard = _install_network_guard(python, cwd=scratch)
        child_site = network_guard.parent.resolve()
        _run([str(adlife), "--version"], cwd=scratch, network_guard_token="version")
        doctor = _run(
            [str(adlife), "--format", "json", "doctor", "--offline"],
            cwd=scratch,
            network_guard_token="doctor",
        )
        doctor_document = _expect_json(doctor, "doctor --offline")
        _verify_offline_doctor(doctor_document)

        catalog_list = _expect_json(
            _run(
                [str(adlife), "--format", "json", "city-catalog", "list"],
                cwd=scratch,
                network_guard_token="catalog-list",
            ),
            "city-catalog list",
        )
        cities = catalog_list.get("cities")
        if not isinstance(cities, list) or not any(
            isinstance(entry, dict) and entry.get("city_id") == "fictional-grid-v2"
            for entry in cities
        ):
            _refuse("city-catalog list", "fictional-grid-v2 was not listed")
        catalog_entry = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-catalog",
                    "show",
                    "fictional-grid-v2",
                ],
                cwd=scratch,
                network_guard_token="catalog-show",
            ),
            "city-catalog show",
        )
        _expect_value(catalog_entry, "city_id", "fictional-grid-v2", "city-catalog show")
        _expect_value(catalog_entry, "qualification", "fictional-fixture", "city-catalog show")
        _expect_value(catalog_entry, "pack_schema_version", 2, "city-catalog show")
        catalog_sha256 = _expect_sha256(catalog_entry, "pack_sha256", "city-catalog show")

        places_path = scratch / "fictional-grid-v2-places.json"
        _write_fictional_place_set(places_path, city_sha256=catalog_sha256)
        places = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-places",
                    "validate",
                    str(places_path),
                    "--city-id",
                    "fictional-grid-v2",
                    "--agents",
                    "2",
                    "--days",
                    "1",
                    "--seed",
                    "42",
                ],
                cwd=scratch,
                network_guard_token="places-validate",
            ),
            "city-places validate",
        )
        _expect_value(places, "valid", True, "city-places validate")
        _expect_value(places, "city_id", "fictional-grid-v2", "city-places validate")
        place_set_sha256 = _expect_sha256(places, "place_set_sha256", "city-places validate")

        spatial_path = scratch / "fictional-grid-v2-spatial-campaign.json"
        _write_fictional_spatial_campaign(spatial_path, city_sha256=catalog_sha256)
        spatial = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-campaign",
                    "validate",
                    str(spatial_path),
                    "--city-id",
                    "fictional-grid-v2",
                ],
                cwd=scratch,
                network_guard_token="campaign-validate",
            ),
            "city-campaign validate",
        )
        _expect_value(spatial, "valid", True, "city-campaign validate")
        _expect_value(
            spatial,
            "scenario_id",
            "clean-room-spatial",
            "city-campaign validate",
        )
        _expect_value(spatial, "city_sha256", catalog_sha256, "city-campaign validate")
        _expect_value(spatial, "campaign_count", 1, "city-campaign validate")
        _expect_value(spatial, "placement_count", 2, "city-campaign validate")
        _expect_value(spatial, "billboard_count", 1, "city-campaign validate")
        _expect_value(spatial, "phone_count", 1, "city-campaign validate")
        scenario_sha256 = _expect_sha256(spatial, "scenario_sha256", "city-campaign validate")
        binding_error = spatial.get("max_billboard_binding_error_meters")
        if type(binding_error) is not float or not 0 <= binding_error <= 1:
            _refuse("city-campaign validate", "billboard binding error is outside 0..1 m")

        response_path = scratch / "fictional-grid-v2-spatial-response.json"
        _write_fictional_spatial_response(
            response_path,
            city_sha256=catalog_sha256,
            scenario_sha256=scenario_sha256,
        )
        response_input_bytes = response_path.read_bytes()
        if (
            not response_input_bytes.endswith(b"\n")
            or response_input_bytes.count(b"\n") != 1
            or b"\r" in response_input_bytes
        ):
            _refuse("spatial response input", "expected one canonical UTF-8 JSON line")
        response_input_sha256 = sha256(response_input_bytes[:-1]).hexdigest()

        _run(
            [str(adlife), "init", "smoke-study"],
            cwd=scratch,
            network_guard_token="legacy-init",
        )
        run = _run(
            [
                str(adlife),
                "--format",
                "json",
                "run",
                "smoke-study",
                "--campaign",
                "campaigns/demo-phone.yaml",
                "--run-id",
                "smoke",
                "--mode",
                "rules",
                "--days",
                "1",
                "--population-size",
                "2",
                "--headless",
            ],
            cwd=scratch,
            network_guard_token="legacy-run",
        )
        run_document = _expect_json(run, "run")
        _expect_value(run_document, "status", "completed", "run")

        report = _run(
            [str(adlife), "--format", "json", "report", "smoke-study", "smoke"],
            cwd=scratch,
            network_guard_token="legacy-report",
        )
        _expect_json(report, "report")

        html = scratch / "smoke-study" / "reports" / "smoke.html"
        if not html.is_file() or html.stat().st_size < 10_000:
            print(f"expected a substantive self-contained report at {html}", file=sys.stderr)
            raise SystemExit(1)

        city_run = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-run",
                    "--city-id",
                    "fictional-grid-v2",
                    "--places",
                    str(places_path),
                    "--spatial-campaign",
                    str(spatial_path),
                    "--spatial-response",
                    str(response_path),
                    "--output-root",
                    "city-output",
                    "--run-id",
                    "catalog-smoke",
                    "--agents",
                    "2",
                    "--days",
                    "1",
                    "--seed",
                    "42",
                ],
                cwd=scratch,
                network_guard_token="catalog-run",
            ),
            "city-run --city-id",
        )
        _expect_value(city_run, "run_id", "catalog-smoke", "city-run --city-id")
        _expect_value(city_run, "run_schema_version", 6, "city-run --city-id")
        _expect_value(city_run, "city_id", "fictional-grid-v2", "city-run --city-id")
        _expect_value(city_run, "city_sha256", catalog_sha256, "city-run --city-id")
        _expect_value(
            city_run,
            "place_set_sha256",
            place_set_sha256,
            "city-run --city-id",
        )
        _expect_sha256(city_run, "place_assignments_sha256", "city-run --city-id")
        _expect_value(
            city_run,
            "scenario_sha256",
            scenario_sha256,
            "city-run --city-id",
        )
        for key in ("opportunity_stream_sha256", "opportunity_summary_sha256"):
            _expect_sha256(city_run, key, "city-run --city-id")
        opportunity_bytes = city_run.get("opportunity_stream_bytes")
        if type(opportunity_bytes) is not int or opportunity_bytes <= 0:
            _refuse("city-run --city-id", "opportunity stream is empty or has invalid size")
        opportunity_count = city_run.get("opportunity_count")
        opportunity_counts = city_run.get("opportunity_counts")
        if type(opportunity_count) is not int or opportunity_count <= 0:
            _refuse("city-run --city-id", "opportunity stream contains no opportunities")
        if not isinstance(opportunity_counts, dict) or (
            opportunity_counts.get("opportunity_count") != opportunity_count
        ):
            _refuse("city-run --city-id", "opportunity summary does not match the stream")
        _expect_value(
            city_run,
            "opportunity_claim_scope",
            "synthetic-opportunity-not-impression",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "attention_model_id",
            "spatial-attention-v1",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "attention_claim_scope",
            "synthetic-attention-not-observed-behavior",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "attention_notice_probability",
            0.5,
            "city-run --city-id",
        )
        for key in ("attention_stream_sha256", "attention_summary_sha256"):
            _expect_sha256(city_run, key, "city-run --city-id")
        attention_bytes = city_run.get("attention_stream_bytes")
        if type(attention_bytes) is not int or attention_bytes <= 0:
            _refuse("city-run --city-id", "attention stream is empty or has invalid size")
        impression_count = city_run.get("impression_count")
        noticed_count = city_run.get("noticed_count")
        attention_counts = city_run.get("attention_counts")
        if impression_count != opportunity_count:
            _refuse("city-run --city-id", "impressions do not match opportunities")
        if (
            type(noticed_count) is not int
            or type(impression_count) is not int
            or not 0 <= noticed_count <= impression_count
        ):
            _refuse("city-run --city-id", "attention funnel counts are invalid")
        if not isinstance(attention_counts, dict) or (
            attention_counts.get("impression_count") != impression_count
            or attention_counts.get("noticed_count") != noticed_count
        ):
            _refuse("city-run --city-id", "attention summary does not match the stream")
        _expect_value(
            city_run,
            "response_model_id",
            "spatial-response-v1",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "response_claim_scope",
            "synthetic-response-not-observed-behavior",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "response_input_sha256",
            response_input_sha256,
            "city-run --city-id",
        )
        response_digests = {
            key: _expect_sha256(city_run, key, "city-run --city-id")
            for key in (
                "response_stream_sha256",
                "response_state_sha256",
                "response_summary_sha256",
            )
        }
        response_stream_bytes = city_run.get("response_stream_bytes")
        response_count = city_run.get("response_count")
        state_update_count = city_run.get("state_update_count")
        final_state_count = city_run.get("final_state_count")
        if type(response_stream_bytes) is not int or response_stream_bytes <= 0:
            _refuse("city-run --city-id", "response stream is empty or has invalid size")
        _expect_response_funnel(
            response_count=response_count,
            state_update_count=state_update_count,
            noticed_count=noticed_count,
            label="city-run --city-id",
        )
        _expect_value(city_run, "response_campaign_count", 1, "city-run --city-id")
        _expect_value(city_run, "final_state_count", 2, "city-run --city-id")
        response_counts = city_run.get("response_counts")
        expected_response_counts = {
            "schema_version": 1,
            "response_count": response_count,
            "state_update_count": state_update_count,
            "roadside_response_count": (
                response_counts.get("roadside_response_count")
                if isinstance(response_counts, dict)
                else None
            ),
            "phone_response_count": (
                response_counts.get("phone_response_count")
                if isinstance(response_counts, dict)
                else None
            ),
            "campaign_count": 1,
            "final_state_count": 2,
        }
        roadside_response_count = expected_response_counts["roadside_response_count"]
        phone_response_count = expected_response_counts["phone_response_count"]
        if (
            response_counts != expected_response_counts
            or type(roadside_response_count) is not int
            or type(phone_response_count) is not int
            or roadside_response_count + phone_response_count != response_count
        ):
            _refuse("city-run --city-id", "response summary does not match the stream")
        _expect_value(city_run, "frame_count", 1_440, "city-run --city-id")
        _expect_value(city_run, "position_count", 2_880, "city-run --city-id")

        city_run_directory = scratch / "city-output" / "city-runs" / "catalog-smoke"
        response_artifacts = {
            "inputs/spatial-response.json": response_input_bytes,
            "outputs/spatial-responses.jsonl": None,
            "outputs/response-state.json": None,
            "outputs/response-summary.json": None,
        }
        for relative_path, exact_bytes in response_artifacts.items():
            artifact = city_run_directory / relative_path
            if not artifact.is_file() or artifact.is_symlink():
                _refuse("city-run --city-id", f"missing safe response artifact {relative_path}")
            artifact_bytes = artifact.read_bytes()
            if exact_bytes is not None and artifact_bytes != exact_bytes:
                _refuse("city-run --city-id", f"{relative_path} is not the canonical input")
            if (
                relative_path == "outputs/spatial-responses.jsonl"
                and len(artifact_bytes) != response_stream_bytes
            ):
                _refuse("city-run --city-id", "response stream size does not match its receipt")
        for relative_path, digest_key in (
            ("outputs/spatial-responses.jsonl", "response_stream_sha256"),
            ("outputs/response-state.json", "response_state_sha256"),
            ("outputs/response-summary.json", "response_summary_sha256"),
        ):
            if (
                sha256((city_run_directory / relative_path).read_bytes()).hexdigest()
                != (response_digests[digest_key])
            ):
                _refuse("city-run --city-id", f"{relative_path} hash does not match its receipt")
        source_hashes = _artifact_hashes(city_run_directory)
        metrics = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-metrics",
                    "city-output",
                    "catalog-smoke",
                ],
                cwd=scratch,
                network_guard_token="catalog-metrics",
            ),
            "city-metrics",
        )
        _expect_value(metrics, "model_id", "spatial-metrics-v1", "city-metrics")
        _expect_value(
            metrics,
            "claim_scope",
            "synthetic-metrics-not-observed-outcomes",
            "city-metrics",
        )
        _expect_value(metrics, "source_run_schema_version", 6, "city-metrics")
        for key in ("scenario_sha256", "city_sha256", "trace_sha256"):
            _expect_value(metrics, key, city_run.get(key), "city-metrics")
        _expect_sha256(metrics, "opportunity_structure_sha256", "city-metrics")
        overall = metrics.get("overall")
        if not isinstance(overall, dict):
            _refuse("city-metrics", "overall metric series is missing")
        for name, expected in (
            ("opportunity_count", opportunity_count),
            ("impression_count", impression_count),
            ("noticed_count", noticed_count),
        ):
            receipt = overall.get(name)
            if not isinstance(receipt, dict) or receipt.get("numerator") != expected:
                _refuse("city-metrics", f"{name} numerator does not match persisted evidence")
            if receipt.get("denominator") != 1 or receipt.get("value") != float(expected):
                _refuse("city-metrics", f"{name} receipt is invalid")
        metric_sources = _source_artifacts(metrics)
        expected_metric_sources = {
            "outputs/spatial-opportunities.jsonl",
            "outputs/spatial-attention.jsonl",
        }
        if metric_sources != expected_metric_sources:
            _refuse(
                "city-metrics",
                "schema-v6 metrics must remain attention-only and cite both evidence streams",
            )
        _expect_artifacts_unchanged(city_run_directory, source_hashes, "city-metrics")

        comparison = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-compare",
                    "city-output",
                    "catalog-smoke",
                    "catalog-smoke",
                ],
                cwd=scratch,
                network_guard_token="catalog-compare",
            ),
            "city-compare A/A",
        )
        _expect_value(
            comparison,
            "model_id",
            "spatial-metrics-comparison-v1",
            "city-compare A/A",
        )
        _expect_value(
            comparison,
            "claim_scope",
            "synthetic-comparison-not-causal-or-observed-effect",
            "city-compare A/A",
        )
        _expect_value(
            comparison,
            "classification",
            "matched-opportunity-structure",
            "city-compare A/A",
        )
        for key in ("control_run_id", "treatment_run_id"):
            _expect_value(comparison, key, "catalog-smoke", "city-compare A/A")
        if comparison.get("control") != metrics or comparison.get("treatment") != metrics:
            _refuse("city-compare A/A", "source metric snapshots do not match city-metrics")
        zero_names = (
            "opportunity_count",
            "impression_count",
            "noticed_count",
            "opportunity_reach",
            "impression_reach",
            "noticed_reach",
            "impression_frequency",
            "notice_rate",
        )
        delta_series = [comparison.get("overall")]
        channels = comparison.get("channels")
        if not isinstance(channels, list) or len(channels) != 2:
            _refuse("city-compare A/A", "canonical channel deltas are missing")
        delta_series.extend(channels)
        for series in delta_series:
            if not isinstance(series, dict) or any(
                _finite_float(series.get(name), "city-compare A/A") != 0.0 for name in zero_names
            ):
                _refuse("city-compare A/A", "A/A comparison contains a nonzero delta")
        _expect_artifacts_unchanged(city_run_directory, source_hashes, "city-compare")

        replay = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-replay",
                    "city-output",
                    "catalog-smoke",
                ],
                cwd=scratch,
                network_guard_token="catalog-replay",
            ),
            "city-replay",
        )
        _expect_value(replay, "run_id", "catalog-smoke", "city-replay")
        _expect_value(replay, "identical", True, "city-replay")
        for key in (
            "city_sha256",
            "place_set_sha256",
            "place_assignments_sha256",
            "trace_sha256",
            "scenario_sha256",
            "opportunity_stream_sha256",
            "opportunity_summary_sha256",
            "opportunity_stream_bytes",
            "opportunity_count",
            "opportunity_counts",
            "opportunity_claim_scope",
            "attention_model_id",
            "attention_claim_scope",
            "attention_notice_probability",
            "attention_stream_sha256",
            "attention_summary_sha256",
            "attention_stream_bytes",
            "impression_count",
            "noticed_count",
            "attention_counts",
            "response_model_id",
            "response_claim_scope",
            "response_input_sha256",
            "response_stream_sha256",
            "response_state_sha256",
            "response_summary_sha256",
            "response_stream_bytes",
            "response_count",
            "state_update_count",
            "response_campaign_count",
            "final_state_count",
            "response_counts",
            "frame_count",
            "position_count",
        ):
            _expect_value(replay, key, city_run.get(key), "city-replay")
        _expect_artifacts_unchanged(city_run_directory, source_hashes, "city-replay")
        if type(response_count) is not int or type(final_state_count) is not int:
            _refuse("installed city viewer", "response counts are not exact integers")
        viewer_probe = _verify_installed_viewer(
            python,
            root=scratch / "city-output",
            run_id="catalog-smoke",
            child_site=child_site,
            response_count=response_count,
            final_state_count=final_state_count,
            cwd=scratch,
        )
        _expect_artifacts_unchanged(city_run_directory, source_hashes, "installed city viewer")
        spatial_report_bytes = _certify_spatial_study(
            adlife,
            scratch=scratch,
            places_path=places_path,
            spatial_path=spatial_path,
            response_path=response_path,
            catalog_sha256=catalog_sha256,
            place_set_sha256=place_set_sha256,
            scenario_sha256=scenario_sha256,
            response_input_sha256=response_input_sha256,
        )
        _verify_network_guard_log(
            network_guard,
            adlife=adlife,
            viewer_probe=viewer_probe,
            required_tokens=(*_ADLIFE_GUARD_TOKENS, _VIEWER_GUARD_TOKEN),
            viewer_token=_VIEWER_GUARD_TOKEN,
        )
        print(
            "smoke ok: "
            f"report at {html.stat().st_size} bytes; "
            "schema-v6 response API/UI verified without network; "
            "catalog fictional-grid-v2 metrics, A/A comparison, and replay verified; "
            f"two-seed response study and {spatial_report_bytes}-byte static report verified"
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse-locked-dependencies",
        action="store_true",
        help=(
            "Reuse dependencies from the invoking locked environment for a network-free "
            "test; the wheel itself is still installed into a fresh environment."
        ),
    )
    parser.add_argument("wheel", type=Path, help="Path to the built wheel to verify.")
    arguments = parser.parse_args()
    if not arguments.wheel.is_file():
        print(f"wheel not found: {arguments.wheel}", file=sys.stderr)
        raise SystemExit(2)
    smoke(
        arguments.wheel.resolve(),
        reuse_locked_dependencies=arguments.reuse_locked_dependencies,
    )


if __name__ == "__main__":
    main()
