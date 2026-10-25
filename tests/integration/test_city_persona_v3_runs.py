"""Persona prompt v3 end to end: new runs answer in character, saved runs stay exact.

New runs built through the server path freeze contract V4 and rebuild identical persona
requests on every load and replay. A run saved before the change (the owner's real
guided run, when it is present on this machine) still loads and replays byte-for-byte.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from adlife.adapters.cognition.city_prompts_v2 import build_city_cognition_payload_v2
from adlife.city import runs
from adlife.city.run_store import CityRunStore
from adlife.city.run_story import build_run_story
from adlife.city.workbench_binding import build_city_cognition_run_context_v2
from adlife.city.workbench_input import WorkbenchRunDraftV4, parse_workbench_run_input_json
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.domain import city_cognition_v2 as domain
from adlife.core.domain.city_cognition_v2 import CityCognitionInputV2
from adlife.core.domain.serialization import canonical_json
from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10, _save_prepared
from tests.unit.city.run_story_fixtures import default_study_draft
from tests.unit.city.test_city_run_preparation import _v2_arguments
from tests.unit.cognition.test_city_openai_compatible import _profile

REAL_RUN = Path(
    os.environ.get(
        "ADLIFE_SAVED_RUN_V2",
        r"C:\your workspace\github\adlife\adlife-workspace\city-runs\guided-music-1024-1053",
    )
)


def _server_path_run(*, mode="mock", profile=None, run_id="persona-v3", limit=None):
    # The server freezes CURRENT_CITY_COGNITION_CAUSAL_CONTRACT for every new run.
    draft = default_study_draft(
        run_id=run_id, mode=mode, profile=profile, funded_response_limit=limit or 1000
    )
    return construct_workbench_run(
        WorkbenchRunDraftV4.model_validate_json(json.dumps(draft)),
        causal_contract=domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    )


def test_server_path_runs_freeze_contract_v4_and_rebuild_identical_persona_requests(
    tmp_path: Path,
) -> None:
    validated = _server_path_run()
    cognition = validated.workbench_input.draft.cognition
    assert cognition.causal_contract is not None
    assert (
        cognition.causal_contract.fingerprint
        == domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V4.fingerprint
    )
    assert cognition.max_request_bytes == 16384
    staged, prepared = _prepared_v10(tmp_path, mode="mock", validated=validated)
    requests = staged.cognition_requests
    assert requests
    population = validated.workbench_input.draft.population
    display_name_of = {persona.persona_id: persona.display_name for persona in population.personas}
    persona_of = {item.agent_id: item.persona_id for item in population.assignments}
    for request in requests:
        persona = request.causal_identity.persona
        assert persona is not None
        # The card is the persona of the request's own agent, not just any persona in the run.
        agent_id = request.causal_identity.agent.agent_id
        assert persona.person.name == display_name_of[persona_of[agent_id]], agent_id
        assert persona.person.background is not None
    # Verification and replay rebuild the identical requests from the saved run.
    _save_prepared(tmp_path, prepared)
    stored = CityRunStore(tmp_path).load("persona-v3")
    assert stored.cognition_input == staged.cognition_input
    assert runs.replay_city_run(stored).identical is True
    context = build_city_cognition_run_context_v2(
        stored.workbench_input,
        pack=stored.pack,
        spatial_scenario=stored.spatial_scenario,
        response_input=stored.response_input,
        baseline_evaluation=staged.baseline_evaluation,
    )
    assert context.profiles is not None
    rebuilt = runs.build_city_cognition_requests_v2(
        context,
        staged.mobility,
        staged.baseline_evaluation,
        causal_contract=stored.cognition_input.causal_contract,
        funded_response_limit=cognition.funded_response_limit,
    )
    assert [canonical_json(r) for r in rebuilt] == [canonical_json(r) for r in requests]
    saved = json.loads(
        (tmp_path / "city-runs" / "persona-v3" / "inputs" / "cognition.json").read_bytes()
    )
    assert saved["max_request_bytes"] == 16384
    assert saved["causal_contract"]["prompt_contract_id"] == "city-cognition-persona-prompt-v3"
    # The story keeps working and names the contract it was answered under.
    story = build_run_story(stored)
    assert story.ai is not None
    assert story.ai.contract.prompt_contract_id == "city-cognition-persona-prompt-v3"


def test_a_v4_provider_request_body_answers_as_the_person() -> None:
    profile = _profile(
        model_id="qwen-example-model",
        max_output_tokens=256,
        transient_retries=3,
        repair_attempts=2,
        disable_thinking=True,
    )
    validated = _server_path_run(mode="remote", profile=profile, limit=1000)
    staged = runs.prepare_city_scientific_run_v2(
        validated.pack, provider_profile=profile, **_v2_arguments(validated)
    )
    request = staged.cognition_requests[-1]
    payload = build_city_cognition_payload_v2(request, staged.cognition_input)
    messages = payload["messages"]
    assert messages[0]["content"] == domain.CITY_COGNITION_PROMPT_CONTRACT_V3_TEXT
    data = messages[1]["content"].split("\n")[2]
    block = json.loads(data)
    assert set(block) == {
        "ad",
        "disclosure",
        "feelings_now",
        "history",
        "person",
        "request_id",
        "situation",
    }
    persona = request.causal_identity.persona
    assert block["person"]["name"] == persona.person.name
    assert block["request_id"] == request.request_id
    wire = canonical_json(payload)
    for internal in ("person-0", "fictional-launch", "target_interests", "baseline_"):
        assert internal not in wire


@pytest.mark.skipif(not REAL_RUN.is_dir(), reason="the owner's saved guided run is not here")
def test_the_real_saved_v2_run_still_loads_and_replays_identically(tmp_path: Path) -> None:
    # Read-only source: copy the run into a fresh temporary workspace first.
    target = tmp_path / "city-runs" / REAL_RUN.name
    shutil.copytree(REAL_RUN, target)
    stored = CityRunStore(tmp_path).load(REAL_RUN.name)
    assert runs.replay_city_run(stored).identical is True
    cognition = target / "inputs" / "cognition.json"
    parsed = CityCognitionInputV2.model_validate_json(cognition.read_bytes())
    assert (canonical_json(parsed) + "\n").encode() == cognition.read_bytes()
    assert (
        parsed.causal_contract.fingerprint
        == domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2.fingerprint
    )
    workbench = target / "inputs" / "workbench.json"
    document = parse_workbench_run_input_json(workbench.read_bytes())
    assert (canonical_json(document) + "\n").encode() == workbench.read_bytes()
    assert build_run_story(stored).ai is not None
