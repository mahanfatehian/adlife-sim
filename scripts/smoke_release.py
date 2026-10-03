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
import json
import os
import shutil
import site
import subprocess
import sys
import tempfile
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Never

_FICTIONAL_CREATIVE_SHA256 = sha256(b"fictional clean-room creative").hexdigest()
_NETWORK_GUARD_LOG = "adlife-smoke-network-guard.log"
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
with Path(__file__).with_name("adlife-smoke-network-guard.log").open(
    "a", encoding="utf-8"
) as _guard_log:
    _guard_log.write(f"{sys.argv[0]}\\n")
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
        base_url="http://city.test",
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
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=300,
        env=_subprocess_environment(allow_network_environment=allow_network_environment),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = completed.stdout + completed.stderr
        print(f"FAILED ({completed.returncode}): {' '.join(command)}", file=sys.stderr)
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


def _refuse(label: str, detail: str) -> Never:
    print(f"{label}: {detail}", file=sys.stderr)
    raise SystemExit(1)


def _expect_value(document: dict[str, object], key: str, expected: object, label: str) -> None:
    actual = document.get(key)
    if type(actual) is not type(expected) or actual != expected:
        _refuse(label, f"expected {key}={expected!r}, got {actual!r}")


def _expect_sha256(document: dict[str, object], key: str, label: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or len(value) != 64:
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
    """Hash every regular file in a bounded smoke artifact without following links."""
    if not directory.is_dir() or directory.is_symlink():
        _refuse("city artifact", "expected a non-symlink saved run directory")
    hashes: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            _refuse("city artifact", "saved run contains a symlink")
        if not path.is_file():
            continue
        digest = sha256()
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
        hashes[path.relative_to(directory).as_posix()] = digest.hexdigest()
    if not hashes:
        _refuse("city artifact", "saved run contains no files")
    return hashes


def _expect_artifacts_unchanged(
    directory: Path,
    expected: dict[str, str],
    boundary: str,
) -> None:
    if _artifact_hashes(directory) != expected:
        _refuse(boundary, "read-only operation modified source city artifacts")


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


def _verify_network_guard_log(guard: Path, *, adlife: Path, viewer_probe: Path) -> None:
    """Prove both the console launcher and isolated viewer loaded sitecustomize."""
    log = guard.with_name(_NETWORK_GUARD_LOG)
    if not log.is_file() or log.is_symlink() or log.stat().st_size > 64 * 1024:
        raise RuntimeError("smoke network guard activation log is missing or unsafe")
    entries = tuple(line for line in log.read_text(encoding="utf-8").splitlines() if line)
    launcher_names = {adlife.name.casefold(), "adlife", "adlife.exe"}
    if not any(Path(entry).name.casefold() in launcher_names for entry in entries):
        raise RuntimeError("installed adlife launcher did not load the smoke network guard")
    if not any(Path(entry).resolve() == viewer_probe.resolve() for entry in entries):
        raise RuntimeError("isolated viewer probe did not load the smoke network guard")


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
        _run([str(adlife), "--version"], cwd=scratch)
        doctor = _run([str(adlife), "--format", "json", "doctor", "--offline"], cwd=scratch)
        doctor_document = _expect_json(doctor, "doctor --offline")

        catalog_list = _expect_json(
            _run(
                [str(adlife), "--format", "json", "city-catalog", "list"],
                cwd=scratch,
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
            ),
            "city-catalog show",
        )
        _expect_value(catalog_entry, "city_id", "fictional-grid-v2", "city-catalog show")
        _expect_value(catalog_entry, "qualification", "fictional-fixture", "city-catalog show")
        _expect_value(catalog_entry, "pack_schema_version", 2, "city-catalog show")
        catalog_sha256 = catalog_entry.get("pack_sha256")
        if not isinstance(catalog_sha256, str) or len(catalog_sha256) != 64:
            _refuse("city-catalog show", "pack_sha256 is not a SHA-256 digest")

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
            ),
            "city-places validate",
        )
        _expect_value(places, "valid", True, "city-places validate")
        _expect_value(places, "city_id", "fictional-grid-v2", "city-places validate")
        place_set_sha256 = places.get("place_set_sha256")
        if not isinstance(place_set_sha256, str) or len(place_set_sha256) != 64:
            _refuse("city-places validate", "place_set_sha256 is not a SHA-256 digest")

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
        scenario_sha256 = spatial.get("scenario_sha256")
        if not isinstance(scenario_sha256, str) or len(scenario_sha256) != 64:
            _refuse("city-campaign validate", "scenario_sha256 is not a SHA-256 digest")
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

        _run([str(adlife), "init", "smoke-study"], cwd=scratch)
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
        )
        run_document = _expect_json(run, "run")
        status = str(run_document.get("status", run_document.get("state", "")))
        if "complete" not in status:
            print(f"run document does not record completion: {run_document}", file=sys.stderr)
            raise SystemExit(1)

        report = _run(
            [str(adlife), "--format", "json", "report", "smoke-study", "smoke"],
            cwd=scratch,
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
        assignments_sha256 = city_run.get("place_assignments_sha256")
        if not isinstance(assignments_sha256, str) or len(assignments_sha256) != 64:
            _refuse("city-run --city-id", "place_assignments_sha256 is not a SHA-256 digest")
        _expect_value(
            city_run,
            "scenario_sha256",
            scenario_sha256,
            "city-run --city-id",
        )
        for key in ("opportunity_stream_sha256", "opportunity_summary_sha256"):
            value = city_run.get(key)
            if not isinstance(value, str) or len(value) != 64:
                _refuse("city-run --city-id", f"{key} is not a SHA-256 digest")
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
            value = city_run.get(key)
            if not isinstance(value, str) or len(value) != 64:
                _refuse("city-run --city-id", f"{key} is not a SHA-256 digest")
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
        structure_sha256 = metrics.get("opportunity_structure_sha256")
        if not isinstance(structure_sha256, str) or len(structure_sha256) != 64:
            _refuse("city-metrics", "opportunity structure hash is not a SHA-256 digest")
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
            if not isinstance(series, dict) or any(series.get(name) != 0.0 for name in zero_names):
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
        _verify_network_guard_log(network_guard, adlife=adlife, viewer_probe=viewer_probe)
        _ = doctor_document  # parsed: the JSON contract held
        print(
            "smoke ok: "
            f"report at {html.stat().st_size} bytes; "
            "schema-v6 response API/UI verified without network; "
            "catalog fictional-grid-v2 metrics, A/A comparison, and replay verified"
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
