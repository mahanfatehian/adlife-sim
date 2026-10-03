"""Exercise smoke workspace lifetime without creating another environment."""

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from scripts import smoke_release


def _expected_response_document(*, city_sha256: str, scenario_sha256: str) -> dict[str, object]:
    return {
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
                "creative_sha256": (
                    "1b286665d9b12264f37cfcbb7b9938660fa2ad496de2fa86c2f78fe802b7f0ec"
                ),
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


def test_smoke_generates_one_canonical_fictional_response_input(tmp_path: Path) -> None:
    path = tmp_path / "response.json"
    expected = _expected_response_document(city_sha256="a" * 64, scenario_sha256="e" * 64)

    smoke_release._write_fictional_spatial_response(
        path,
        city_sha256="a" * 64,
        scenario_sha256="e" * 64,
    )

    expected_bytes = (
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    assert path.read_bytes() == expected_bytes


def test_smoke_subprocess_environment_refuses_source_checkout_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "untrusted-checkout/src")
    monkeypatch.setenv("PYTHONHOME", "untrusted-python-home")
    monkeypatch.setenv("PYTHONUSERBASE", "untrusted-user-base")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "untrusted-launcher")
    monkeypatch.setenv("VIRTUAL_ENV", "untrusted-active-environment")
    monkeypatch.setenv("CONDA_PREFIX", "untrusted-conda")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted-proxy.invalid")
    monkeypatch.setenv("ADLIFE_API_KEY", "secret-value")
    monkeypatch.setenv("LD_PRELOAD", "untrusted-library")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "untrusted-library")
    monkeypatch.setenv("UV_OFFLINE", "1")

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs.get("env")
        assert isinstance(environment, dict)
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "strict"
        assert "PYTHONPATH" not in environment
        assert "PYTHONHOME" not in environment
        assert "PYTHONUSERBASE" not in environment
        assert "__PYVENV_LAUNCHER__" not in environment
        assert "VIRTUAL_ENV" not in environment
        assert "CONDA_PREFIX" not in environment
        assert "HTTPS_PROXY" not in environment
        assert "ADLIFE_API_KEY" not in environment
        assert "LD_PRELOAD" not in environment
        assert "DYLD_INSERT_LIBRARIES" not in environment
        assert environment["PYTHONNOUSERSITE"] == "1"
        assert environment["PYTHONSAFEPATH"] == "1"
        assert environment["PYTHONUTF8"] == "1"
        assert environment["PYTHONIOENCODING"] == "utf-8"
        assert environment["UV_OFFLINE"] == "1"
        return subprocess.CompletedProcess(command, 0, "isolated", "")

    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)

    assert smoke_release._run(["child-python", "--version"]).stdout == "isolated"


def test_smoke_subprocess_environment_scrubs_case_insensitive_names_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_release.os,
        "environ",
        {
            "PythonPath": "shadow-checkout",
            "PythonHome": "shadow-runtime",
            "PythonOptimize": "2",
            "service_TOKEN": "token",
            "service_SECRET": "secret",
            "service_PASSWORD": "password",
            "service_CREDENTIAL": "credential",
            "API_KEY_21ST": "provider-key",
            "nested_vendor_api_key_value": "nested-provider-key",
            "AWS_ACCESS_KEY_ID": "cloud-access-key",
            "AWS_SECRET_ACCESS_KEY": "cloud-secret-key",
            "vendor_client_secret_nested": "nested-client-secret",
            "GOOGLE_APPLICATION_CREDENTIALS": "credential-file.json",
            "https_proxy": "http://proxy.invalid",
            "UV_INDEX_URL": "https://user:password@registry.invalid/simple",
            "uv_extra_index_url": "https://extra.invalid/simple",
            "PIP_INDEX_URL": "https://user:password@registry.invalid/simple",
            "pip_extra_index_url": "https://extra.invalid/simple",
            "SSH_AUTH_SOCK": "agent.sock",
            "GIT_ASKPASS": "credential-helper",
            "UV_KEYRING_PROVIDER": "subprocess",
            "UV_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "SAFE_SETTING": "preserved",
        },
    )

    environment = smoke_release._subprocess_environment()

    assert (
        not {
            "PythonPath",
            "PythonHome",
            "PythonOptimize",
            "service_TOKEN",
            "service_SECRET",
            "service_PASSWORD",
            "service_CREDENTIAL",
            "API_KEY_21ST",
            "nested_vendor_api_key_value",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "vendor_client_secret_nested",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "https_proxy",
            "UV_INDEX_URL",
            "uv_extra_index_url",
            "PIP_INDEX_URL",
            "pip_extra_index_url",
            "SSH_AUTH_SOCK",
            "GIT_ASKPASS",
            "UV_KEYRING_PROVIDER",
        }
        & environment.keys()
    )
    assert environment["UV_OFFLINE"] == "1"
    assert environment["TOKENIZERS_PARALLELISM"] == "false"
    assert environment["SAFE_SETTING"] == "preserved"


def test_smoke_installer_environment_preserves_only_package_registry_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_release.os,
        "environ",
        {
            "PythonPath": "shadow-checkout",
            "HTTPS_PROXY": "http://registry-proxy.invalid",
            "no_proxy": "127.0.0.1,localhost",
            "UV_INDEX_URL": "https://registry.invalid/simple",
            "UV_EXTRA_INDEX_URL": "https://extra.invalid/simple",
            "UV_INDEX_PRIVATE_USERNAME": "registry-user",
            "UV_INDEX_PRIVATE_PASSWORD": "registry-password",
            "PIP_INDEX_URL": "https://registry.invalid/simple",
            "PIP_EXTRA_INDEX_URL": "https://extra.invalid/simple",
            "PIP_TRUSTED_HOST": "registry.invalid",
            "REGISTRY_TOKEN": "registry-token",
            "ADLIFE_API_KEY": "provider-key",
            "API_KEY_21ST": "provider-key",
            "AWS_ACCESS_KEY_ID": "cloud-access-key",
            "AWS_SECRET_ACCESS_KEY": "cloud-secret-key",
            "SSH_AUTH_SOCK": "agent.sock",
            "GIT_ASKPASS": "credential-helper",
            "UV_KEYRING_PROVIDER": "subprocess",
            "SAFE_SETTING": "preserved",
        },
    )

    environment = smoke_release._subprocess_environment(allow_network_environment=True)

    assert "PythonPath" not in environment
    assert environment["HTTPS_PROXY"] == "http://registry-proxy.invalid"
    assert environment["no_proxy"] == "127.0.0.1,localhost"
    assert environment["UV_INDEX_URL"] == "https://registry.invalid/simple"
    assert environment["UV_EXTRA_INDEX_URL"] == "https://extra.invalid/simple"
    assert environment["UV_INDEX_PRIVATE_USERNAME"] == "registry-user"
    assert environment["UV_INDEX_PRIVATE_PASSWORD"] == "registry-password"
    assert environment["PIP_INDEX_URL"] == "https://registry.invalid/simple"
    assert environment["PIP_EXTRA_INDEX_URL"] == "https://extra.invalid/simple"
    assert environment["PIP_TRUSTED_HOST"] == "registry.invalid"
    assert environment["SAFE_SETTING"] == "preserved"
    assert (
        not {
            "REGISTRY_TOKEN",
            "ADLIFE_API_KEY",
            "API_KEY_21ST",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "SSH_AUTH_SOCK",
            "GIT_ASKPASS",
            "UV_KEYRING_PROVIDER",
        }
        & environment.keys()
    )


def test_smoke_json_contract_refuses_success_diagnostics() -> None:
    completed = subprocess.CompletedProcess(["adlife"], 0, "{}\n", "unsafe warning\n")

    with pytest.raises(SystemExit):
        smoke_release._expect_json(completed, "clean JSON")


def test_smoke_json_contract_refuses_extra_stdout_lines() -> None:
    completed = subprocess.CompletedProcess(["adlife"], 0, "{}\n\n", "")

    with pytest.raises(SystemExit):
        smoke_release._expect_json(completed, "one JSON line")


def test_smoke_network_guard_blocks_socket_operations(
    tmp_path: Path,
) -> None:
    venv = tmp_path / "venv"
    created = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    python = venv / scripts / ("python.exe" if sys.platform == "win32" else "python")

    guard = smoke_release._install_network_guard(python, cwd=tmp_path)
    probe = (
        "import builtins,sys;"
        "assert builtins.__adlife_smoke_network_guard__;"
        "assert builtins.__adlife_smoke_network_guard_self_test__;"
        "\nsys.audit('socket.connect', None, ('127.0.0.1', 1))"
        "\nsys.audit('socket.sendto', None, ('::1', 1))"
        "\nsys.audit('socket.sendmsg', None, ('127.0.0.1', 1))"
        "\ntry:\n sys.audit('socket.connect', None, ('203.0.113.1', 443))"
        "\nexcept RuntimeError:\n pass"
        "\nelse:\n raise AssertionError('external connection was not blocked')"
        "\ntry:\n sys.audit('socket.getaddrinfo', 'example.invalid', 443, 0, 0, 0)"
        "\nexcept RuntimeError:\n pass"
        "\nelse:\n raise AssertionError('DNS lookup was not blocked')"
        "\nfor event,args in ("
        "('socket.gethostbyname',('example.invalid',)),"
        "('socket.gethostbyname_ex',('example.invalid',)),"
        "('socket.gethostbyaddr',('203.0.113.1',)),"
        "('socket.getnameinfo',(('203.0.113.1',443),0)),"
        "('socket.sendmsg',(None,('203.0.113.1',443)))):"
        "\n try:\n  sys.audit(event,*args)"
        "\n except RuntimeError:\n  pass"
        "\n else:\n  raise AssertionError(f'{event} was not blocked')"
        "\nprint('blocked')"
    )
    completed = subprocess.run(
        [str(python), "-I", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == "blocked\n"
    assert guard.read_text(encoding="utf-8").endswith("\n")


def test_smoke_response_funnel_refuses_zero_updates_for_positive_responses() -> None:
    with pytest.raises(SystemExit):
        smoke_release._expect_response_funnel(
            response_count=1,
            state_update_count=0,
            noticed_count=1,
            label="city-run --city-id",
        )


def test_smoke_viewer_probe_checks_guard_before_installed_imports_and_metrics() -> None:
    source = smoke_release._VIEWER_PROBE_SOURCE

    assert source.index("\n_verify_guard()\n\nimport adlife") < source.index(
        "from adlife.city.analysis"
    )
    assert 'metrics_payload = await _json(client, "/api/spatial-metrics")' in source
    assert 'assert metrics_payload == metrics.model_dump(mode="json")' in source


def test_smoke_network_guard_refuses_to_replace_sitecustomize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_site = tmp_path / "venv" / "Lib" / "site-packages"
    child_site.mkdir(parents=True)
    existing = child_site / "sitecustomize.py"
    existing.write_text("# dependency-owned\n", encoding="utf-8")
    monkeypatch.setattr(
        smoke_release,
        "_child_site_packages",
        lambda python, **kwargs: child_site,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="sitecustomize"):
        smoke_release._install_network_guard(tmp_path / "venv" / "python")

    assert existing.read_text(encoding="utf-8") == "# dependency-owned\n"


def test_smoke_discovers_child_purelib_with_the_child_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "venv"
    python = environment / "Scripts" / "python.exe"
    purelib = environment / "Lib" / "site-packages"
    purelib.mkdir(parents=True)

    def run_child(
        command: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert command == [
            str(python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ]
        assert cwd == tmp_path
        return subprocess.CompletedProcess(command, 0, f"{purelib}\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)

    assert smoke_release._child_site_packages(python, cwd=tmp_path) == purelib.resolve()


@pytest.mark.parametrize(
    "boundary",
    ("city-metrics", "city-compare", "city-replay", "installed city viewer"),
)
def test_smoke_refuses_source_mutation_after_each_read_only_boundary(
    boundary: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_directory = tmp_path / "city-runs" / "study"
    run_directory.mkdir(parents=True)
    artifact = run_directory / "run.json"
    artifact.write_bytes(b"before\n")
    source_hashes = smoke_release._artifact_hashes(run_directory)
    artifact.write_bytes(b"after\n")

    with pytest.raises(SystemExit):
        smoke_release._expect_artifacts_unchanged(run_directory, source_hashes, boundary)

    assert boundary in capsys.readouterr().err


def test_smoke_subprocesses_do_not_create_console_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        expected = 0x08000000 if sys.platform == "win32" else 0
        assert kwargs.get("creationflags", 0) == expected
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(command, 0, "captured", "")

    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)
    assert smoke_release._run(["adlife", "--version"]).stdout == "captured"


def test_smoke_subprocess_flag_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("creationflags") == 0
        return subprocess.CompletedProcess(command, 0, "captured", "")

    monkeypatch.delattr(smoke_release.subprocess, "CREATE_NO_WINDOW", raising=False)
    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)

    assert smoke_release._run(["adlife", "--version"]).stdout == "captured"


def test_smoke_uses_the_running_supported_interpreter_without_fetching_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def record_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(smoke_release, "_uv_executable", lambda: "uv")
    monkeypatch.setattr(smoke_release, "_run", record_command)
    smoke_release._create_interpreter(tmp_path)
    assert commands == [["uv", "venv", "--python", sys.executable, str(tmp_path / "venv")]]


def test_smoke_cleans_workspace_and_returns_no_dead_artifact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    # Environment provisioning is external; run all CLI/report checks for real
    # with the already installed environment so this regression stays offline.
    monkeypatch.setattr(
        smoke_release, "_create_interpreter", lambda path: (Path(sys.executable), None)
    )
    monkeypatch.setattr(smoke_release, "_install_wheel", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        smoke_release,
        "_install_network_guard",
        lambda *args, **kwargs: workspace / "sitecustomize.py",
        raising=False,
    )
    monkeypatch.setattr(
        smoke_release,
        "_verify_installed_viewer",
        lambda *args, **kwargs: workspace / "verify-installed-city-viewer.py",
        raising=False,
    )
    monkeypatch.setattr(
        smoke_release,
        "_verify_network_guard_log",
        lambda *args, **kwargs: None,
    )

    result = smoke_release.smoke(tmp_path / "unused.whl")

    assert not workspace.exists()
    assert result is None


def test_smoke_cleans_workspace_when_installation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))

    def fail_provisioning(path: Path) -> None:
        raise RuntimeError("environment unavailable")

    monkeypatch.setattr(smoke_release, "_create_interpreter", fail_provisioning)
    with pytest.raises(RuntimeError, match="environment unavailable"):
        smoke_release.smoke(tmp_path / "unused.whl")
    assert not workspace.exists()


def test_smoke_exercises_verified_catalog_run_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a wheel smoke that omits any packaged-catalog consumer boundary."""
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    scratch = workspace / "scratch"
    calls: list[tuple[list[str], Path | None]] = []
    city_sha256 = "a" * 64
    place_sha256 = "c" * 64
    assignments_sha256 = "d" * 64
    scenario_sha256 = "e" * 64
    trace_sha256 = "b" * 64
    opportunity_stream_sha256 = "f" * 64
    opportunity_summary_sha256 = "1" * 64
    attention_stream_sha256 = "2" * 64
    attention_summary_sha256 = "3" * 64
    structure_sha256 = "4" * 64
    response_stream = b'{"event_type":"spatial.response"}\n'
    response_state = b'{"model_id":"spatial-response-state-v1"}\n'
    response_summary = b'{"model_id":"spatial-response-artifact-v1"}\n'
    response_stream_sha256 = sha256(response_stream).hexdigest()
    response_state_sha256 = sha256(response_state).hexdigest()
    response_summary_sha256 = sha256(response_summary).hexdigest()
    response_input_document = _expected_response_document(
        city_sha256=city_sha256,
        scenario_sha256=scenario_sha256,
    )
    response_input_canonical = json.dumps(
        response_input_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    response_input_sha256 = sha256(response_input_canonical).hexdigest()
    opportunity_counts = {
        "opportunity_count": 4,
        "roadside_billboard_count": 0,
        "mobile_feed_count": 4,
    }
    attention_counts = {
        "opportunity_count": 4,
        "impression_count": 4,
        "noticed_count": 2,
        "roadside_impression_count": 0,
        "roadside_noticed_count": 0,
        "phone_impression_count": 4,
        "phone_noticed_count": 2,
    }
    opportunity_receipt = {
        "numerator": 4,
        "denominator": 1,
        "value": 4.0,
        "source_artifacts": ["outputs/spatial-opportunities.jsonl"],
    }
    impression_receipt = {
        "numerator": 4,
        "denominator": 1,
        "value": 4.0,
        "source_artifacts": ["outputs/spatial-attention.jsonl"],
    }
    noticed_receipt = {
        "numerator": 2,
        "denominator": 1,
        "value": 2.0,
        "source_artifacts": ["outputs/spatial-attention.jsonl"],
    }
    metrics_document: dict[str, object] = {
        "model_id": "spatial-metrics-v1",
        "claim_scope": "synthetic-metrics-not-observed-outcomes",
        "source_run_schema_version": 6,
        "scenario_sha256": scenario_sha256,
        "city_sha256": city_sha256,
        "trace_sha256": trace_sha256,
        "opportunity_structure_sha256": structure_sha256,
        "overall": {
            "opportunity_count": opportunity_receipt,
            "impression_count": impression_receipt,
            "noticed_count": noticed_receipt,
        },
    }
    response_counts = {
        "schema_version": 1,
        "response_count": 2,
        "state_update_count": 2,
        "roadside_response_count": 0,
        "phone_response_count": 2,
        "campaign_count": 1,
        "final_state_count": 2,
    }

    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    monkeypatch.setattr(
        smoke_release,
        "_create_interpreter",
        lambda path: (workspace / "venv" / "Scripts" / "python.exe", None),
    )
    monkeypatch.setattr(smoke_release, "_install_wheel", lambda *args, **kwargs: None)
    child_site = workspace / "venv" / "Lib" / "site-packages"
    child_site.mkdir(parents=True)
    installed_package = child_site / "adlife"
    installed_package.mkdir()
    installed_module = installed_package / "__init__.py"
    installed_module.write_text("", encoding="utf-8")
    artifact_hash_call_positions: list[int] = []
    real_artifact_hashes = smoke_release._artifact_hashes

    def record_artifact_hashes(directory: Path) -> dict[str, str]:
        artifact_hash_call_positions.append(len(calls))
        return real_artifact_hashes(directory)

    monkeypatch.setattr(smoke_release, "_artifact_hashes", record_artifact_hashes)

    def run_child(
        command: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command[1:], cwd))
        arguments = command[1:]
        document: dict[str, object] = {}
        guard_log = child_site / "adlife-smoke-network-guard.log"
        if (child_site / "sitecustomize.py").is_file():
            entry = arguments[1] if len(arguments) > 1 and arguments[0] == "-I" else command[0]
            with guard_log.open("a", encoding="utf-8") as target:
                target.write(f"{entry}\n")
        if arguments[:2] == ["-I", "-c"]:
            return subprocess.CompletedProcess(command, 0, f"{child_site}\n", "")
        if arguments and arguments[0] == "-I":
            assert arguments == [
                "-I",
                str(scratch / "verify-installed-city-viewer.py"),
                str(scratch / "city-output"),
                "catalog-smoke",
                str(child_site),
            ]
            assert (child_site / "sitecustomize.py").is_file()
            document = {
                "run_schema_version": 6,
                "response_model_id": "spatial-response-v1",
                "claim_scope": "synthetic-response-not-observed-behavior",
                "summary_model_id": "spatial-response-artifact-v1",
                "state_model_id": "spatial-response-state-v1",
                "state_scope": "final-end-of-run-not-scrubbed-minute",
                "response_count": 2,
                "final_state_count": 2,
                "adlife_module_path": str(installed_module),
                "child_site_path": str(child_site),
                "module_origin_verified": True,
                "network_guard_verified": True,
                "ui_verified": True,
            }
        elif arguments == ["--format", "json", "doctor", "--offline"]:
            document = {"status": "ok"}
        elif arguments[:3] == ["--format", "json", "run"]:
            document = {"status": "complete"}
        elif arguments[:3] == ["--format", "json", "report"]:
            report = scratch / "smoke-study" / "reports" / "smoke.html"
            report.parent.mkdir(parents=True)
            report.write_text("x" * 10_001, encoding="utf-8")
        elif arguments == ["--format", "json", "city-catalog", "list"]:
            document = {
                "cities": [
                    {
                        "city_id": "fictional-grid-v2",
                        "qualification": "fictional-fixture",
                        "pack_schema_version": 2,
                    }
                ]
            }
        elif arguments == [
            "--format",
            "json",
            "city-catalog",
            "show",
            "fictional-grid-v2",
        ]:
            document = {
                "city_id": "fictional-grid-v2",
                "qualification": "fictional-fixture",
                "pack_schema_version": 2,
                "pack_sha256": city_sha256,
            }
        elif arguments[:4] == ["--format", "json", "city-places", "validate"]:
            place_path = Path(arguments[4])
            place_document = json.loads(place_path.read_text(encoding="utf-8"))
            assert place_document["city_id"] == "fictional-grid-v2"
            assert place_document["city_sha256"] == city_sha256
            assert {place["kind"] for place in place_document["places"]} == {
                "home",
                "workplace",
                "leisure",
            }
            assert all(
                place["provenance"]["method"] == "operator-authored-fictional"
                for place in place_document["places"]
            )
            document = {
                "valid": True,
                "city_id": "fictional-grid-v2",
                "place_set_sha256": place_sha256,
            }
        elif arguments[:4] == ["--format", "json", "city-campaign", "validate"]:
            scenario_path = Path(arguments[4])
            scenario_document = json.loads(scenario_path.read_text(encoding="utf-8"))
            assert scenario_document["scenario_id"] == "clean-room-spatial"
            assert scenario_document["city_id"] == "fictional-grid-v2"
            assert scenario_document["city_sha256"] == city_sha256
            assert {item["channel"] for item in scenario_document["placements"]} == {
                "roadside-billboard",
                "mobile-feed",
            }
            document = {
                "valid": True,
                "scenario_id": "clean-room-spatial",
                "scenario_sha256": scenario_sha256,
                "city_id": "fictional-grid-v2",
                "city_sha256": city_sha256,
                "campaign_count": 1,
                "placement_count": 2,
                "billboard_count": 1,
                "phone_count": 1,
                "max_billboard_binding_error_meters": 0.0,
            }
        elif arguments[:3] == ["--format", "json", "city-run"]:
            response_path = Path(arguments[arguments.index("--spatial-response") + 1])
            assert json.loads(response_path.read_text(encoding="utf-8")) == response_input_document
            run_directory = scratch / "city-output" / "city-runs" / "catalog-smoke"
            (run_directory / "inputs").mkdir(parents=True)
            (run_directory / "outputs").mkdir()
            (run_directory / "run.json").write_text("manifest", encoding="utf-8")
            (run_directory / "inputs" / "city.json").write_text("city", encoding="utf-8")
            (run_directory / "outputs" / "spatial-attention.jsonl").write_text(
                "attention", encoding="utf-8"
            )
            (run_directory / "inputs" / "spatial-response.json").write_bytes(
                response_input_canonical + b"\n"
            )
            (run_directory / "outputs" / "spatial-responses.jsonl").write_bytes(response_stream)
            (run_directory / "outputs" / "response-state.json").write_bytes(response_state)
            (run_directory / "outputs" / "response-summary.json").write_bytes(response_summary)
            document = {
                "run_id": "catalog-smoke",
                "run_schema_version": 6,
                "city_id": "fictional-grid-v2",
                "city_sha256": city_sha256,
                "place_set_sha256": place_sha256,
                "place_assignments_sha256": assignments_sha256,
                "trace_sha256": trace_sha256,
                "scenario_sha256": scenario_sha256,
                "opportunity_stream_sha256": opportunity_stream_sha256,
                "opportunity_summary_sha256": opportunity_summary_sha256,
                "opportunity_stream_bytes": 1_234,
                "opportunity_count": 4,
                "opportunity_counts": opportunity_counts,
                "opportunity_claim_scope": "synthetic-opportunity-not-impression",
                "attention_model_id": "spatial-attention-v1",
                "attention_claim_scope": "synthetic-attention-not-observed-behavior",
                "attention_notice_probability": 0.5,
                "attention_stream_sha256": attention_stream_sha256,
                "attention_summary_sha256": attention_summary_sha256,
                "attention_stream_bytes": 2_468,
                "impression_count": 4,
                "noticed_count": 2,
                "attention_counts": attention_counts,
                "response_model_id": "spatial-response-v1",
                "response_claim_scope": "synthetic-response-not-observed-behavior",
                "response_input_sha256": response_input_sha256,
                "response_stream_sha256": response_stream_sha256,
                "response_state_sha256": response_state_sha256,
                "response_summary_sha256": response_summary_sha256,
                "response_stream_bytes": len(response_stream),
                "response_count": 2,
                "state_update_count": 2,
                "response_campaign_count": 1,
                "final_state_count": 2,
                "response_counts": response_counts,
                "frame_count": 1_440,
                "position_count": 2_880,
                "directory": str(scratch / "city-output" / "city-runs" / "catalog-smoke"),
            }
        elif arguments == [
            "--format",
            "json",
            "city-metrics",
            "city-output",
            "catalog-smoke",
        ]:
            document = metrics_document
        elif arguments == [
            "--format",
            "json",
            "city-compare",
            "city-output",
            "catalog-smoke",
            "catalog-smoke",
        ]:
            zero_series = {
                "opportunity_count": 0.0,
                "impression_count": 0.0,
                "noticed_count": 0.0,
                "opportunity_reach": 0.0,
                "impression_reach": 0.0,
                "noticed_reach": 0.0,
                "impression_frequency": 0.0,
                "notice_rate": 0.0,
            }
            document = {
                "model_id": "spatial-metrics-comparison-v1",
                "claim_scope": "synthetic-comparison-not-causal-or-observed-effect",
                "classification": "matched-opportunity-structure",
                "control_run_id": "catalog-smoke",
                "treatment_run_id": "catalog-smoke",
                "control_opportunity_structure_sha256": structure_sha256,
                "treatment_opportunity_structure_sha256": structure_sha256,
                "control": metrics_document,
                "treatment": metrics_document,
                "overall": zero_series,
                "channels": [
                    {"channel": "roadside", **zero_series},
                    {"channel": "mobile", **zero_series},
                ],
            }
        elif arguments == [
            "--format",
            "json",
            "city-replay",
            "city-output",
            "catalog-smoke",
        ]:
            document = {
                "run_id": "catalog-smoke",
                "identical": True,
                "city_sha256": city_sha256,
                "place_set_sha256": place_sha256,
                "place_assignments_sha256": assignments_sha256,
                "trace_sha256": trace_sha256,
                "scenario_sha256": scenario_sha256,
                "opportunity_stream_sha256": opportunity_stream_sha256,
                "opportunity_summary_sha256": opportunity_summary_sha256,
                "opportunity_stream_bytes": 1_234,
                "opportunity_count": 4,
                "opportunity_counts": opportunity_counts,
                "opportunity_claim_scope": "synthetic-opportunity-not-impression",
                "attention_model_id": "spatial-attention-v1",
                "attention_claim_scope": "synthetic-attention-not-observed-behavior",
                "attention_notice_probability": 0.5,
                "attention_stream_sha256": attention_stream_sha256,
                "attention_summary_sha256": attention_summary_sha256,
                "attention_stream_bytes": 2_468,
                "impression_count": 4,
                "noticed_count": 2,
                "attention_counts": attention_counts,
                "response_model_id": "spatial-response-v1",
                "response_claim_scope": "synthetic-response-not-observed-behavior",
                "response_input_sha256": response_input_sha256,
                "response_stream_sha256": response_stream_sha256,
                "response_state_sha256": response_state_sha256,
                "response_summary_sha256": response_summary_sha256,
                "response_stream_bytes": len(response_stream),
                "response_count": 2,
                "state_update_count": 2,
                "response_campaign_count": 1,
                "final_state_count": 2,
                "response_counts": response_counts,
                "frame_count": 1_440,
                "position_count": 2_880,
            }
        return subprocess.CompletedProcess(command, 0, json.dumps(document) + "\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)

    smoke_release.smoke(tmp_path / "unused.whl")

    invoked = [arguments for arguments, _cwd in calls]
    assert ["--format", "json", "city-catalog", "list"] in invoked
    assert [
        "--format",
        "json",
        "city-catalog",
        "show",
        "fictional-grid-v2",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-places",
        "validate",
        str(scratch / "fictional-grid-v2-places.json"),
        "--city-id",
        "fictional-grid-v2",
        "--agents",
        "2",
        "--days",
        "1",
        "--seed",
        "42",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-campaign",
        "validate",
        str(scratch / "fictional-grid-v2-spatial-campaign.json"),
        "--city-id",
        "fictional-grid-v2",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-run",
        "--city-id",
        "fictional-grid-v2",
        "--places",
        str(scratch / "fictional-grid-v2-places.json"),
        "--spatial-campaign",
        str(scratch / "fictional-grid-v2-spatial-campaign.json"),
        "--spatial-response",
        str(scratch / "fictional-grid-v2-spatial-response.json"),
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
    ] in invoked
    assert [
        "--format",
        "json",
        "city-replay",
        "city-output",
        "catalog-smoke",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-metrics",
        "city-output",
        "catalog-smoke",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-compare",
        "city-output",
        "catalog-smoke",
        "catalog-smoke",
    ] in invoked
    viewer_call_position = next(
        index
        for index, (arguments, _cwd) in enumerate(calls)
        if len(arguments) > 1 and arguments[0] == "-I" and arguments[1] != "-c"
    )
    boundary_positions = [
        next(index for index, (arguments, _cwd) in enumerate(calls) if marker in arguments) + 1
        for marker in ("city-metrics", "city-compare", "city-replay")
    ]
    boundary_positions.append(viewer_call_position + 1)
    assert len(artifact_hash_call_positions) == 5
    assert artifact_hash_call_positions[1:] == boundary_positions
    assert all(cwd == scratch for _arguments, cwd in calls)
    assert not workspace.exists()


def test_offline_smoke_installs_exact_wheel_without_resolving_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def record_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(smoke_release, "_run", record_command)

    smoke_release._install_wheel(Path("child-python"), "uv", Path("adlife.whl"), no_deps=True)

    assert calls == [
        [
            "uv",
            "pip",
            "install",
            "--python",
            "child-python",
            "--quiet",
            "--no-deps",
            "adlife.whl",
        ]
    ]


def test_offline_smoke_exposes_only_locked_host_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    python = venv / "Scripts" / "python.exe"
    child_site = venv / "Lib" / "site-packages"
    child_site.mkdir(parents=True)
    host_site = tmp_path / "host" / "site-packages"
    host_site.mkdir(parents=True)

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, f"{child_site}\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)
    monkeypatch.setattr(smoke_release.site, "getsitepackages", lambda: [str(host_site)])

    smoke_release._expose_locked_dependencies(python)

    assert (child_site / "adlife-smoke-locked-dependencies.pth").read_text(
        encoding="utf-8"
    ) == f"{host_site.resolve()}\n"


def test_offline_smoke_refuses_child_site_outside_the_created_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    python = venv / "Scripts" / "python.exe"
    outside = tmp_path / "outside" / "site-packages"
    outside.mkdir(parents=True)

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, f"{outside}\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)

    with pytest.raises(RuntimeError, match="outside the smoke environment"):
        smoke_release._expose_locked_dependencies(python)
