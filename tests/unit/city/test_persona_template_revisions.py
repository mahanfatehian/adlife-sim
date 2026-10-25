"""A new packaged template text never reaches a run saved before it.

A guided run copies each chosen template into its frozen ``inputs/workbench.json``, so a
run keeps the persona text it was made with. Loading, verifying and replaying a saved run
read only that frozen copy; the story reads the packaged catalog for one thing alone, to
recognize a template persona and show its translated name, tagline and colour.

``EARLIER_BACKGROUNDS`` is the packaged text before the backgrounds were enriched for
prompt contract v3 (the AI answers as the person), exactly as runs saved earlier hold it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adlife.city import workbench_personas
from adlife.city.run_store import CityRunStore
from adlife.city.run_story import build_run_story
from adlife.city.runs import replay_city_run
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.unit.city.run_story_fixtures import (
    _mock_plan,
    _publish,
    _validated_draft,
    default_study_draft,
)

EARLIER_BACKGROUNDS = {
    "persona-001": (
        "A wholly invented character who collects puzzle ideas and enjoys trying small "
        "craft projects. This story and MBTI label are descriptive scenario context only; "
        "numeric assumptions are supplied explicitly below."
    ),
    "persona-002": (
        "A wholly invented character who keeps imagined folktales and likes patient "
        "revision. Neither this biography nor the optional personality label determines "
        "response values or a mobility schedule."
    ),
}


def _earlier_study(run_id: str) -> dict[str, Any]:
    document = default_study_draft(run_id=run_id)
    for persona in document["population"]["personas"]:
        persona["background"] = EARLIER_BACKGROUNDS[persona["persona_id"]]
    return document


@pytest.fixture(scope="module")
def earlier_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    workspace = tmp_path_factory.mktemp("earlier-templates") / "workspace"
    root = prepare_workbench_workspace(workspace).root
    validated = _validated_draft(_earlier_study("earlier-templates"))
    return root, _publish(root, validated, profile=None, plan=_mock_plan)


def test_the_packaged_text_differs_from_the_text_earlier_runs_hold() -> None:
    current = {
        item.persona_id: item.background for item in load_persona_template_catalog().templates
    }
    for persona_id, earlier in EARLIER_BACKGROUNDS.items():
        assert current[persona_id] != earlier
        assert current[persona_id] is not None
        # The enriched text keeps the earlier opening sentence, then adds a whole life.
        assert current[persona_id].startswith(earlier.split(". ", maxsplit=1)[0] + ". ")


def test_a_saved_run_keeps_its_own_frozen_persona_copy(earlier_run) -> None:
    _root, stored = earlier_run
    personas = stored.workbench_input.draft.population.personas
    assert {item.persona_id: item.background for item in personas} == EARLIER_BACKGROUNDS


def test_load_and_replay_never_read_the_packaged_templates(
    earlier_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stored = earlier_run
    baseline = replay_city_run(stored)
    assert baseline.identical is True
    # Point the package-resource reader at an empty folder: the catalog is now unreadable.
    monkeypatch.setattr(workbench_personas, "files", lambda _package: tmp_path)
    with pytest.raises(workbench_personas.PersonaTemplateCatalogError):
        load_persona_template_catalog()
    reloaded = CityRunStore(root).load(stored.manifest.run_id)
    assert reloaded.workbench_input == stored.workbench_input
    replay = replay_city_run(reloaded)
    assert replay.identical is True
    assert replay == baseline


def test_the_story_still_recognizes_templates_in_runs_saved_with_the_earlier_text(
    earlier_run,
) -> None:
    """Needs the story's template match to ignore the background (``run_story._template_key``).

    Without it, every template persona in a run saved before the enriched backgrounds loses
    its translated name, tagline and template colour in the guide story and the report.
    """
    _root, stored = earlier_run
    story = build_run_story(stored)
    assert [
        (persona.persona_id, persona.template_key, persona.color_index)
        for persona in story.personas
    ] == [("persona-001", "lumo", 0), ("persona-002", "veyra", 1)]
