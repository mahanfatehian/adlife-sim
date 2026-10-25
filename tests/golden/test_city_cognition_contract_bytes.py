"""Runs frozen with the older causal contracts keep every saved byte.

The pinned hashes were produced by the tree BEFORE prompt contract v3 existed (commit
f9ce800) from the same fixtures: a mock-mode run saved under contract V2 (selection V2,
prompt v2) and one under contract V3 (selection V3, prompt v2). Adding the persona prompt
(contract V4) must not move a single byte, request ID, or hash of these documents. The
manifest is compared without its package and Python versions, which describe the build.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city.run_store import CityRunStore
from adlife.city.runs import replay_city_run
from adlife.city.workbench_input import WorkbenchRunDraftV4
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.domain import city_cognition_v2 as domain
from adlife.core.domain.serialization import canonical_json
from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10, _save_prepared
from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

_SHARED = {
    "inputs/agents.json": "dc52a3b9947fbd9822c5a58f9758b321ca959c0810db4c417b9b1a043279150e",
    "inputs/city.json": "b5acf335c731f039f6859a5cc8d1e4dff06276ae17d784081d81f8043abed02f",
    "inputs/spatial-campaign.json": (
        "816d9580e589399276b99c6a49e7b889536da92d6f58bd4f9c4e7e392b76fc7e"
    ),
    "inputs/spatial-response.json": (
        "79bd221aa408e6148fc905c38a43d881b7af5c08e6bf38072cc95bbeef24b7c3"
    ),
    "outputs/attention-summary.json": (
        "531b524f7c1a1eed59b70018d6cbff7a8769084c20c4f2e1818c6c2aa095fdb6"
    ),
    "outputs/opportunity-summary.json": (
        "e58613d3262c7617690bcd6958cd131c104640111c3032f837b93a7be65e83c8"
    ),
    "outputs/spatial-attention.jsonl": (
        "1b6062f02757d9cbd18c757852e7dcf3becf4ecb9daa74b35cd5ea323e180716"
    ),
    "outputs/spatial-opportunities.jsonl": (
        "fb43e70d4765041f2edf0128354f26bf6bb79889e4a2151f54354876da274b2b"
    ),
}
PINNED = {
    "v2": {
        "manifest": "1d80853589ff28ae37a6787230230c1dd293a352a2ce9fbba4711c1cb31dfb2d",
        "requests": (
            "cog-d988d0f56ea8c12f2a07aeac87040460fe4068206daa70f15796d1326c8b8197",
            "cog-f29bd9757f9f638ed556a4dbd803d49d78e585f9147f0584c1ff45e5a4f67818",
            "cog-8fb80b62012c0a5522d98fb60f3749beca3a7becae975722c29151188e79d241",
        ),
        "files": _SHARED
        | {
            "inputs/cognition.json": (
                "4680fa70cf594fca54c7a92972191592cf31e4e1fd8d6dcf9bb2def50a50b6ee"
            ),
            "inputs/workbench.json": (
                "9e123ee1a8db739a58a5a4be4c49935b370d27ed524c2bdbeed08dea5f49846f"
            ),
            "outputs/cognition-summary.json": (
                "fd4897d5f5a88e1ed124e2f5949508db7f856b7c63924a57f16b05e4accf902e"
            ),
            "outputs/cognition.jsonl": (
                "9ecb418ee556bb94737eae4e1e79910b5e93655aa93b4deab106149088322db3"
            ),
            "outputs/response-state.json": (
                "113b64c8774230100d18170905ec08fcaf6e8fb6b76cb91c024dd6aaecd71a00"
            ),
            "outputs/response-summary.json": (
                "c721faa293745006baf54daa2432b285eed677a69297e14a3b1eee1f566dde70"
            ),
            "outputs/spatial-responses.jsonl": (
                "5adc3d9e99c1f78c594357ffc0a433768e094d7665b83253552ca3c6529f6df0"
            ),
        },
    },
    "v3": {
        "manifest": "43e121e1f70f012399b6686fd80bf24043c36497a9effe1008efcde8c337b403",
        "requests": (
            "cog-ab75c02ae08499261d4f08528c7c5e7e4cb6f2e4eb1f0b18069ba6702854eb6a",
            "cog-43c56fefad8143f4555202ab97fa12343ef67bfc7fd2ee614edb0dd8dbd3a4f1",
            "cog-6133a5bb365ab90c056e6edcc6a0faec3cfc49131b755801be5ccdac7eba6829",
        ),
        "files": _SHARED
        | {
            "inputs/cognition.json": (
                "b3b82381262f5a8fbc5b82dcb9546b6bc8de8978fba036b29e7edd90edee109d"
            ),
            "inputs/workbench.json": (
                "851b1f2720e7e34aeb312be95e01539cac775adc4a71ef06c5d2f658a118086c"
            ),
            "outputs/cognition-summary.json": (
                "2f5832194b34830e5132eafde283e1f74697e256450fd9ffffcdbccdd2d3a533"
            ),
            "outputs/cognition.jsonl": (
                "d7f8662cd8c238734911d6705295db4dd601d9ae02513baaa8a5fef7fb010bde"
            ),
            "outputs/response-state.json": (
                "bf5396205935a7474f160aa4f463ea681d3fd2cca4db770cd2072e5ce99d6232"
            ),
            "outputs/response-summary.json": (
                "2ab53decd37acf341d9970b2c06401e32eef1d495d9f8a8f1bea5ed537827f1e"
            ),
            "outputs/spatial-responses.jsonl": (
                "efb1a36f0f1efbd3a51493b2401b8f0db3db1287f7ad2a9646b11bf547c60dec"
            ),
        },
    },
}
CONTRACTS = {
    "v2": (domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2, 60),
    "v3": (domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3, 1000),
}


@pytest.mark.parametrize("name", ["v2", "v3"])
def test_old_contract_runs_keep_every_saved_byte(tmp_path: Path, name: str) -> None:
    contract, limit = CONTRACTS[name]
    document = draft_v4_data(mode="mock", limit=limit)
    document["settings"].update(run_id=f"golden-{name}", days=2, agent_count=3)
    document["population"]["personas"][0]["count"] = 3
    validated = construct_workbench_run(
        WorkbenchRunDraftV4.model_validate_json(json.dumps(document)), causal_contract=contract
    )
    staged, prepared = _prepared_v10(tmp_path, mode="mock", validated=validated)
    directory = _save_prepared(tmp_path, prepared)
    pinned = PINNED[name]
    assert tuple(r.request_id for r in staged.cognition_requests[:3]) == pinned["requests"]
    assert all(r.causal_identity.persona is None for r in staged.cognition_requests)
    files = {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }
    manifest = json.loads((directory / "run.json").read_bytes())
    for key in ("package_version", "python_version"):
        manifest.pop(key)
    assert sha256(canonical_json(manifest).encode()).hexdigest() == pinned["manifest"]
    assert {key: value for key, value in files.items() if key != "run.json"} == pinned["files"]
    # The saved prompt-v2 documents carry no persona and the old request ceiling.
    cognition = json.loads((directory / "inputs" / "cognition.json").read_bytes())
    assert cognition["max_request_bytes"] == 8192
    assert cognition["causal_contract"]["prompt_contract_id"] == "city-cognition-signal-prompt-v2"
    stored = CityRunStore(tmp_path).load(f"golden-{name}")
    assert replay_city_run(stored).identical is True
