"""V10 storage is selected by the manifest and reconstructs frozen V2 evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city import run_store as store_module
from adlife.city import runs
from adlife.city.run_store import CityRunStore
from adlife.core.domain.city_cognition_v2 import CityCognitionInputV2
from adlife.core.domain.city_run import CityRunManifestV10, build_city_cognition_manifest_receipt_v2
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import CorruptRunArtifact
from adlife.core.simulation.city_cognition_v2 import (
    build_city_budget_skipped_result_v2,
    build_city_cognition_terminal_record_v2,
    build_city_fallback_result_v2,
    build_city_mock_result_v2,
    build_city_rules_result_v2,
    city_cognition_terminal_lines_v2,
    validate_city_cognition_evaluation_v2,
)
from adlife.core.simulation.spatial_response_v2 import (
    spatial_cognition_state_document_v2,
    spatial_response_state_document_v2,
    spatial_response_v2_lines,
    spatial_rule_state_document_v2,
    summarize_spatial_response_artifact_v2,
)
from tests.unit.city.test_city_run_preparation import (
    _provider_revision,
    _v2_arguments,
    _validated_v4,
)

V10_INPUTS = {
    "agents.json",
    "city.json",
    "cognition.json",
    "spatial-campaign.json",
    "spatial-response.json",
    "workbench.json",
}
V10_OUTPUTS = {
    "attention-summary.json",
    "cognition-summary.json",
    "cognition.jsonl",
    "opportunity-summary.json",
    "response-state.json",
    "response-summary.json",
    "spatial-attention.jsonl",
    "spatial-opportunities.jsonl",
    "spatial-responses.jsonl",
}
V10_PATHS = (
    {"run.json"}
    | {f"inputs/{name}" for name in V10_INPUTS}
    | {f"outputs/{name}" for name in V10_OUTPUTS}
)


def _bytes(model):
    return (canonical_json(model) + "\n").encode("utf-8")


def _prepared_v10(
    root: Path,
    *,
    mode="rules",
    source=None,
    empty=False,
    agents=3,
    days=2,
    run_id="artifact-v10",
    mutation=None,
    validated=None,
):
    """Use the real pure pipeline; no service, HTTP job, or storage schema shim."""
    root.mkdir(parents=True, exist_ok=True)
    profile = _provider_revision(root, mode=mode) if mode in {"local", "remote"} else None

    def mutate(document):
        document["settings"].update(run_id=run_id, days=days)
        if empty:
            document["scenario"]["phone"]["opportunity_probability_per_minute"] = 0.0
        if mutation is not None:
            mutation(document)

    if validated is None:
        validated = _validated_v4(
            mode=mode,
            limit=10 if mode in {"local", "remote"} else 60,
            profile=profile,
            agents=agents,
            mutation=mutate,
        )
    staged = runs.prepare_city_scientific_run_v2(
        validated.pack, provider_profile=profile, **_v2_arguments(validated)
    )
    frozen = staged.cognition_input
    records = []
    for index, request in enumerate(staged.cognition_requests):
        selected_source = source or (mode if mode in {"rules", "mock"} else "provider")
        reason = None
        attempts = 0
        usage = {}
        if not frozen.reservations[index].funded:
            selected_source, reason = "budget-skipped", "budget-skipped"
            result = build_city_budget_skipped_result_v2(request)
        elif selected_source == "rules":
            result = build_city_rules_result_v2(request)
        elif selected_source == "mock":
            result = build_city_mock_result_v2(request, policy=frozen.causal_contract.mock_policy)
        elif selected_source == "fallback":
            reason, attempts = "timeout-exhausted", 2
            result = build_city_fallback_result_v2(request, reason=reason)
        else:
            attempts = 1 if selected_source == "provider" else 0
            result = build_city_rules_result_v2(request).model_copy(
                update={
                    "affect": "positive",
                    "relevance": "high",
                    "credibility": "high",
                    "salience": "high",
                }
            )
            # Cache usage is recorded historical usage, not clamped to a reservation.
            usage = {"prompt_tokens": 900, "completion_tokens": 700 if source == "cache" else 50}
        records.append(
            build_city_cognition_terminal_record_v2(
                request,
                frozen,
                selection_index=index,
                source=selected_source,
                result=result,
                attempt_count=attempts,
                reason=reason,
                **usage,
            )
        )
    evaluation = validate_city_cognition_evaluation_v2(
        frozen,
        staged.cognition_requests,
        tuple(records),
        baseline_evaluation=staged.baseline_evaluation,
    )
    prepared = runs.finalize_city_run_v2(
        staged,
        cognition_input=frozen,
        cognition_evaluation=evaluation,
    )
    return staged, prepared


def _save_prepared(root: Path, prepared, *, store=None, **changes):
    arguments = dict(
        spatial_scenario=prepared.spatial_scenario,
        opportunity_evaluation=prepared.opportunity_evaluation,
        attention_evaluation=prepared.attention_evaluation,
        response_input=prepared.response_input,
        response_evaluation=prepared.response_evaluation,
        workbench_input=prepared.workbench_input,
        cognition_input=prepared.cognition_input,
        cognition_evaluation=prepared.cognition_evaluation,
    )
    arguments.update(changes)
    return (store or CityRunStore(root)).save(
        prepared.manifest,
        prepared.pack,
        prepared.mobility.agents,
        **arguments,
    )


def _saved_v10(root: Path, **options):
    staged, prepared = _prepared_v10(root, **options)
    directory = _save_prepared(root, prepared)
    return staged, prepared, directory


def _json(path: Path):
    return json.loads(path.read_bytes())


def _rewrite(path: Path, document):
    path.write_bytes(_bytes(document))


def _rehash_terminal(document):
    facts = {key: value for key, value in document.items() if key != "record_hash"}
    document["record_hash"] = sha256(canonical_json(facts).encode()).hexdigest()
    return document


def _rebind_terminal_stream(directory: Path, documents):
    """Forge self-consistent file digests so semantic reconstruction must catch drift."""
    stream = b"".join(_bytes(document) for document in documents)
    summary_path = directory / "outputs/cognition-summary.json"
    summary = _json(summary_path)
    summary.update(
        terminal_stream_sha256=sha256(stream).hexdigest(), terminal_stream_bytes=len(stream)
    )
    _rewrite(summary_path, summary)
    manifest = _json(directory / "run.json")
    manifest["cognition"].update(
        terminal_stream_sha256=sha256(stream).hexdigest(),
        terminal_stream_bytes=len(stream),
        summary_sha256=sha256(_bytes(summary)).hexdigest(),
    )
    (directory / "outputs/cognition.jsonl").write_bytes(stream)
    _rewrite(directory / "run.json", manifest)


def test_v10_round_trip_uses_only_v2_manifest_selected_parsers(tmp_path, monkeypatch):
    staged, prepared, directory = _saved_v10(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("V10 entered a V1 cognition/response parser")

    for name in ("_parse_cognition_input", "_parse_cognition_summary", "_parse_cognition_stream"):
        monkeypatch.setattr(store_module, name, forbidden)
    restored = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert type(restored.manifest) is CityRunManifestV10
    assert type(restored.cognition_input) is CityCognitionInputV2
    assert restored.cognition_input == staged.cognition_input
    assert restored.cognition_evaluation == prepared.cognition_evaluation
    assert restored.response_evaluation == prepared.response_evaluation
    assert {
        p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
    } == V10_PATHS
    documents = {
        "inputs/workbench.json": prepared.workbench_input,
        "inputs/cognition.json": prepared.cognition_input,
        "inputs/spatial-response.json": prepared.response_input,
        "outputs/cognition-summary.json": prepared.cognition_evaluation.summary,
        "outputs/response-state.json": spatial_response_state_document_v2(
            prepared.response_evaluation
        ),
        "outputs/response-summary.json": summarize_spatial_response_artifact_v2(
            prepared.response_evaluation
        ),
        "run.json": prepared.manifest,
    }
    for relative, model in documents.items():
        assert (directory / relative).read_bytes() == _bytes(model)
    for relative, lines in (
        (
            "outputs/cognition.jsonl",
            city_cognition_terminal_lines_v2(prepared.cognition_evaluation),
        ),
        (
            "outputs/spatial-responses.jsonl",
            spatial_response_v2_lines(prepared.response_evaluation),
        ),
    ):
        with (directory / relative).open("rb") as source:
            for expected in lines:
                assert source.readline() == expected
            assert source.read(1) == b""
    assert restored.manifest.cognition == build_city_cognition_manifest_receipt_v2(
        staged.cognition_input,
        prepared.cognition_evaluation,
    )
    for document, fingerprint in (
        (
            spatial_rule_state_document_v2(prepared.response_evaluation),
            restored.manifest.rule_state_document_sha256,
        ),
        (
            spatial_cognition_state_document_v2(prepared.response_evaluation),
            restored.manifest.cognition_state_document_sha256,
        ),
    ):
        assert sha256(_bytes(document)).hexdigest() == fingerprint


@pytest.mark.parametrize(
    "mode,source",
    [
        ("rules", None),
        ("mock", None),
        ("local", "provider"),
        ("remote", "provider"),
        ("local", "cache"),
        ("local", "fallback"),
    ],
)
def test_v10_round_trip_preserves_sources_usage_and_rule_purchase_authority(tmp_path, mode, source):
    staged, prepared, _ = _saved_v10(tmp_path, mode=mode, source=source, agents=8)
    stored = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert stored.cognition_evaluation == prepared.cognition_evaluation
    assert stored.response_evaluation == prepared.response_evaluation
    assert {r.source for r in stored.cognition_evaluation.records} == (
        {source or mode} | ({"budget-skipped"} if mode in {"local", "remote"} else set())
    )
    baseline_records = [
        r for r in staged.baseline_evaluation.records if r.event_type == "spatial.response"
    ]
    applied_records = [
        r.baseline
        for r in stored.response_evaluation.records
        if r.event_type == "spatial.applied-response"
    ]
    assert list(map(canonical_json, applied_records)) == list(map(canonical_json, baseline_records))
    assert stored.response_evaluation.final_rule_states == staged.baseline_evaluation.final_states
    assert not any(
        hasattr(state, "purchase_intention")
        for state in stored.response_evaluation.final_cognition_states
    )
    if source == "cache":
        assert all(
            r.completion_tokens == 700
            for r in stored.cognition_evaluation.records
            if r.source == "cache"
        )


@pytest.mark.parametrize("mode", ["rules", "mock", "local"])
def test_v10_empty_selection_requires_empty_streams_and_complete_state_files(tmp_path, mode):
    _, prepared, directory = _saved_v10(tmp_path, mode=mode, empty=True)
    assert prepared.cognition_evaluation.records == ()
    assert {p.name for p in (directory / "inputs").iterdir()} == V10_INPUTS
    assert {p.name for p in (directory / "outputs").iterdir()} == V10_OUTPUTS
    assert (directory / "outputs/cognition.jsonl").read_bytes() == b""
    assert (directory / "outputs/spatial-responses.jsonl").read_bytes() == b""
    restored = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert restored.response_evaluation.final_rule_states
    assert restored.response_evaluation.final_cognition_states
    assert restored.manifest.cognition == prepared.manifest.cognition


@pytest.mark.parametrize("relative", sorted(V10_PATHS - {"run.json"}))
@pytest.mark.parametrize("operation", ["remove", "rename"])
def test_v10_missing_or_renamed_sidecar_is_corruption(tmp_path, relative, operation):
    _, prepared, directory = _saved_v10(tmp_path)
    path = directory / relative
    if operation == "remove":
        path.unlink()
    else:
        path.rename(path.with_suffix(path.suffix + ".renamed"))
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "relative",
    [
        "inputs/extra.json",
        "outputs/extra.json",
        "inputs/cognition-v1.json",
        "outputs/cognition-v1.jsonl",
        "inputs/extra-directory",
        "outputs/extra-directory",
    ],
)
def test_v10_refuses_every_extra_direct_child_sidecar(tmp_path, relative):
    _, prepared, directory = _saved_v10(tmp_path)
    path = directory / relative
    if relative.endswith("directory"):
        path.mkdir()
    else:
        path.write_bytes(b"{}\n")
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize("version", [11, True, 10.0, "10"])
def test_v10_manifest_refusal_precedes_any_sidecar_parser(tmp_path, monkeypatch, version):
    _, prepared, directory = _saved_v10(tmp_path)
    manifest = _json(directory / "run.json")
    manifest["schema_version"] = version
    _rewrite(directory / "run.json", manifest)
    original = CityRunStore._read_document

    def read_manifest_only(self, path, **kwargs):
        assert path.name == "run.json", "manifest refusal read a sidecar"
        return original(self, path, **kwargs)

    monkeypatch.setattr(CityRunStore, "_read_document", read_manifest_only)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "relative",
    [
        "inputs/cognition.json",
        "outputs/cognition-summary.json",
        "outputs/cognition.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
        "outputs/spatial-responses.jsonl",
    ],
)
def test_v10_refuses_v1_sidecar_contents_without_trial_parsing(tmp_path, relative):
    from tests.integration.test_city_cognition_artifacts import _save

    _, _, _, legacy = _save(tmp_path / "v9")
    _, prepared, directory = _saved_v10(tmp_path / "v10")
    (directory / relative).write_bytes((legacy / relative).read_bytes())
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path / "v10").load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "relative",
    [
        "inputs/cognition.json",
        "outputs/cognition-summary.json",
        "outputs/cognition.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
        "outputs/spatial-responses.jsonl",
    ],
)
def test_v9_refuses_v2_sidecar_contents_without_trial_parsing(tmp_path, relative):
    from tests.integration.test_city_cognition_artifacts import _save

    _, _, manifest, legacy = _save(tmp_path / "v9")
    _, _, directory = _saved_v10(tmp_path / "v10")
    (legacy / relative).write_bytes((directory / relative).read_bytes())
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path / "v9").load(manifest.run_id)


@pytest.mark.parametrize(
    "relative",
    [
        "inputs/workbench.json",
        "inputs/spatial-response.json",
        "inputs/cognition.json",
        "outputs/cognition-summary.json",
        "outputs/response-summary.json",
        "outputs/response-state.json",
        "outputs/cognition.jsonl",
        "outputs/spatial-responses.jsonl",
        "run.json",
    ],
)
@pytest.mark.parametrize(
    "corruption", ["truncate", "append", "crlf", "no-lf", "duplicate-key", "utf8", "nonfinite"]
)
def test_v10_refuses_noncanonical_or_malformed_frozen_evidence(tmp_path, relative, corruption):
    _, prepared, directory = _saved_v10(tmp_path)
    path = directory / relative
    original = path.read_bytes()
    changed = {
        "truncate": lambda: original[: len(original) // 2],
        "append": lambda: original + b"{}\n",
        "crlf": lambda: original.replace(b"\n", b"\r\n"),
        "no-lf": lambda: original.removesuffix(b"\n"),
        "duplicate-key": lambda: original.replace(b"{", b'{"schema_version":2,', 1),
        "utf8": lambda: b"\xff" + original,
        "nonfinite": lambda: original.replace(b"{", b'{"invalid":NaN,', 1),
    }[corruption]()
    path.write_bytes(changed)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert path.read_bytes() == changed


@pytest.mark.parametrize("operation", ["reverse", "duplicate", "omit", "blank"])
@pytest.mark.parametrize("relative", ["outputs/cognition.jsonl", "outputs/spatial-responses.jsonl"])
def test_v10_refuses_persisted_stream_permutations_before_normalization(
    tmp_path, operation, relative
):
    _, prepared, directory = _saved_v10(tmp_path)
    path = directory / relative
    lines = path.read_bytes().splitlines(keepends=True)
    assert len(lines) > 1
    changed = {
        "reverse": lambda: list(reversed(lines)),
        "duplicate": lambda: lines + lines[:1],
        "omit": lambda: lines[1:],
        "blank": lambda: [*lines[:1], b"\n", *lines[1:]],
    }[operation]()
    if relative == "outputs/cognition.jsonl" and operation == "reverse":
        _rebind_terminal_stream(directory, [json.loads(line) for line in changed])
    else:
        path.write_bytes(b"".join(changed))
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "field",
    [
        "effective_settings_sha256",
        "budget_allocation_sha256",
        "cognition_input_sha256",
        "summary_sha256",
        "terminal_stream_sha256",
        "causal_contract_sha256",
        "influence_policy_sha256",
    ],
)
def test_v10_reconstructs_complete_receipt_including_settings_and_nonempty_allocations(
    tmp_path, field
):
    _, prepared, directory = _saved_v10(tmp_path, mode="local")
    assert prepared.cognition_input.reservations
    manifest = _json(directory / "run.json")
    assert field in manifest["cognition"]
    manifest["cognition"][field] = "f" * 64
    _rewrite(directory / "run.json", manifest)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


def test_v10_summary_digest_requires_canonical_lf_and_empty_allocation_sentinel(tmp_path):
    _, prepared, directory = _saved_v10(tmp_path, empty=True, mode="local")
    manifest = _json(directory / "run.json")
    manifest["cognition"]["summary_sha256"] = sha256(
        (directory / "outputs/cognition-summary.json").read_bytes()[:-1]
    ).hexdigest()
    _rewrite(directory / "run.json", manifest)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)
    manifest = prepared.manifest.model_dump(mode="json")
    manifest["cognition"]["budget_allocation_sha256"] = "f" * 64
    _rewrite(directory / "run.json", manifest)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_input_sha256", "e" * 64),
        ("request_record_sha256", "e" * 64),
        ("baseline_sha256", "e" * 64),
        ("baseline_response_id", "e" * 64),
        ("baseline_state_update_event_id", "e" * 64),
        ("agent_id", "person-999"),
        ("campaign_id", "foreign-campaign"),
        ("model_minute", 0),
        ("provider_effective_settings_sha256", "e" * 64),
    ],
)
def test_v10_rehashing_terminal_and_manifest_does_not_authorize_cross_binding(
    tmp_path, field, value
):
    _, prepared, directory = _saved_v10(tmp_path, mode="local")
    documents = [
        json.loads(line)
        for line in (directory / "outputs/cognition.jsonl").read_bytes().splitlines()
    ]
    assert documents[0][field] != value
    documents[0][field] = value
    _rehash_terminal(documents[0])
    _rebind_terminal_stream(directory, documents)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "field",
    [
        "response_stream_sha256",
        "response_state_sha256",
        "response_summary_sha256",
        "rule_state_document_sha256",
        "cognition_state_document_sha256",
        "workbench_input_sha256",
        "city_sha256",
    ],
)
def test_v10_refuses_changed_scientific_manifest_binding(tmp_path, field):
    _, prepared, directory = _saved_v10(tmp_path)
    manifest = _json(directory / "run.json")
    assert field in manifest
    manifest[field] = "e" * 64
    _rewrite(directory / "run.json", manifest)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "field", ["cognition_input", "cognition_evaluation", "response_evaluation", "workbench_input"]
)
def test_v10_save_revalidates_bypassed_nested_objects_before_reserving_destination(tmp_path, field):
    _, prepared = _prepared_v10(tmp_path)
    original = getattr(prepared, field)
    bad = type(original).model_construct(
        **{
            name: True if name == "schema_version" else getattr(original, name)
            for name in type(original).model_fields
        }
    )
    with pytest.raises(CorruptRunArtifact):
        _save_prepared(tmp_path, prepared, **{field: bad})
    assert not (tmp_path / "city-runs" / prepared.manifest.run_id).exists()


def test_v10_save_reconstructs_provider_settings_and_nonempty_budget_receipt(tmp_path):
    _, prepared = _prepared_v10(tmp_path, mode="local")
    for field in ("effective_settings_sha256", "budget_allocation_sha256"):
        receipt = prepared.manifest.cognition.model_copy(update={field: "f" * 64})
        forged = replace(
            prepared, manifest=prepared.manifest.model_copy(update={"cognition": receipt})
        )
        with pytest.raises(CorruptRunArtifact):
            _save_prepared(tmp_path, forged)
    assert not (tmp_path / "city-runs" / prepared.manifest.run_id).exists()


@pytest.mark.parametrize(
    "field", sorted(set(CityRunManifestV10.model_fields) - {"package_version", "python_version"})
)
def test_v10_reconstructs_every_manifest_field_against_frozen_sidecars(tmp_path, field):
    _, prepared, directory = _saved_v10(tmp_path)
    manifest = _json(directory / "run.json")
    value = manifest[field]
    if type(value) is int:
        manifest[field] = value + 1
    elif isinstance(value, str):
        manifest[field] = "f" * 64 if len(value) == 64 else "foreign-value"
    elif value is None:
        manifest[field] = "undeclared-value"
    else:
        manifest[field] = {}
    assert manifest[field] != value
    _rewrite(directory / "run.json", manifest)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "relative",
    [
        "inputs/cognition.json",
        "inputs/workbench.json",
        "outputs/cognition.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
        "outputs/spatial-responses.jsonl",
    ],
)
def test_v10_refuses_same_science_sidecar_from_another_run(tmp_path, relative):
    _, first, directory = _saved_v10(tmp_path / "first", run_id="first-run")
    _, _, other = _saved_v10(tmp_path / "second", run_id="second-run", mode="mock")
    (directory / relative).write_bytes((other / relative).read_bytes())
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path / "first").load(first.manifest.run_id)


@pytest.mark.parametrize(
    "case", ["baseline", "applied", "dual-cognition", "dual-rule", "dual-causes"]
)
def test_v10_response_stream_reconstruction_refuses_rehashed_scientific_changes(tmp_path, case):
    _, prepared, directory = _saved_v10(tmp_path, mode="mock")
    path = directory / "outputs/spatial-responses.jsonl"
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    applied = next(
        record for record in records if record["event_type"] == "spatial.applied-response"
    )
    dual = next(
        record for record in records if record["event_type"] == "spatial.dual-state-updated"
    )
    if case == "baseline":
        applied["baseline"]["sentiment_delta"] = 0.123456
    elif case == "applied":
        applied["applied_recall_delta"] = 0.123456
    elif case == "dual-cognition":
        dual["cognition_state"]["synthetic_sentiment"] = 0.123456
    elif case == "dual-rule":
        dual["rule_state"]["purchase_intention"] = 0.123456
    else:
        dual["caused_by_event_ids"] = ["f" * 64]
    stream = b"".join(_bytes(record) for record in records)
    summary = _json(directory / "outputs/response-summary.json")
    summary.update(stream_sha256=sha256(stream).hexdigest(), stream_bytes=len(stream))
    manifest = _json(directory / "run.json")
    manifest.update(
        response_stream_sha256=sha256(stream).hexdigest(),
        response_stream_bytes=len(stream),
        response_summary_sha256=sha256(_bytes(summary)).hexdigest(),
    )
    path.write_bytes(stream)
    _rewrite(directory / "outputs/response-summary.json", summary)
    _rewrite(directory / "run.json", manifest)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize("operation", ["save", "load"])
def test_v10_programming_typeerror_is_not_reclassified_as_artifact_corruption(
    tmp_path, monkeypatch, operation
):
    _, prepared = _prepared_v10(tmp_path)
    if operation == "load":
        _save_prepared(tmp_path, prepared)

    def programming_defect(*_args, **_kwargs):
        raise TypeError("injected builder defect")

    monkeypatch.setattr(store_module, "build_city_cognition_requests_v2", programming_defect)
    with pytest.raises(TypeError, match="injected builder defect"):
        if operation == "save":
            _save_prepared(tmp_path, prepared)
        else:
            CityRunStore(tmp_path).load(prepared.manifest.run_id)


def test_publish_city_run_returns_freshly_loadable_v10_evidence(tmp_path):
    _, prepared = _prepared_v10(tmp_path, mode="mock")
    published = runs.publish_city_run(prepared, root=tmp_path)
    loaded = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert published.manifest == loaded.manifest == prepared.manifest
    assert published.cognition_input == loaded.cognition_input == prepared.cognition_input
    assert (
        published.cognition_evaluation
        == loaded.cognition_evaluation
        == prepared.cognition_evaluation
    )
    assert (
        published.response_evaluation == loaded.response_evaluation == prepared.response_evaluation
    )


def test_v10_load_preserves_historical_package_and_supported_python_metadata(tmp_path):
    _, prepared, directory = _saved_v10(tmp_path)
    manifest = _json(directory / "run.json")
    manifest.update(package_version="0.0.9", python_version="3.11.11")
    _rewrite(directory / "run.json", manifest)
    before = (directory / "run.json").read_bytes()
    stored = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert stored.manifest.package_version == "0.0.9"
    assert stored.manifest.python_version == "3.11.11"
    assert stored.cognition_evaluation == prepared.cognition_evaluation
    assert stored.response_evaluation == prepared.response_evaluation
    assert (directory / "run.json").read_bytes() == before
