from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path
from queue import Empty

import pytest

from adlife.city.catalog import select_catalog_city
from adlife.city.workbench_city_registry import (
    CityPackRegistrationConflict,
    WorkspaceCityRegistry,
)
from adlife.city.workbench_workspace import prepare_workbench_workspace


def _document(city_id: str) -> bytes:
    payload = select_catalog_city("fictional-grid-v2").model_dump(mode="json")
    payload["city_id"] = city_id
    payload["name"] = f"Operator {city_id}"
    return json.dumps(payload, ensure_ascii=False).encode()


def _register_process(
    workspace_path: str,
    city_id: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue[str],
) -> None:
    registry = WorkspaceCityRegistry(prepare_workbench_workspace(Path(workspace_path)))
    start.wait(timeout=20)
    try:
        registry.register(_document(city_id), forbidden_city_ids=frozenset())
    except CityPackRegistrationConflict:
        results.put("conflict")
    except Exception as error:  # pragma: no cover - reported to parent for diagnosis
        results.put(f"unexpected:{type(error).__name__}")
    else:
        results.put("accepted")


def _race(tmp_path: Path, city_ids: tuple[str, str]) -> tuple[list[str], WorkspaceCityRegistry]:
    workspace_path = tmp_path / "workspace"
    WorkspaceCityRegistry(prepare_workbench_workspace(workspace_path)).discover()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_register_process,
            args=(str(workspace_path), city_id, start, results),
        )
        for city_id in city_ids
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)
        assert not process.is_alive()
        assert process.exitcode == 0
    answers: list[str] = []
    for _process in processes:
        try:
            answers.append(results.get(timeout=5))
        except Empty:
            pytest.fail("registry race worker did not report a result")
    return answers, WorkspaceCityRegistry(prepare_workbench_workspace(workspace_path))


def test_real_process_duplicate_registration_has_one_winner(tmp_path: Path) -> None:
    answers, registry = _race(tmp_path, ("race-city", "race-city"))

    assert sorted(answers) == ["accepted", "conflict"]
    assert tuple(pack.city_id for pack in registry.discover()) == ("race-city",)


def test_real_process_capacity_race_cannot_publish_a_fifty_first_pack(tmp_path: Path) -> None:
    registry = WorkspaceCityRegistry(prepare_workbench_workspace(tmp_path / "workspace"))
    for number in range(49):
        registry.register(_document(f"seed-{number:02d}"), forbidden_city_ids=frozenset())

    answers, restarted = _race(tmp_path, ("candidate-a", "candidate-b"))

    assert sorted(answers) == ["accepted", "conflict"]
    assert len(restarted.discover()) == 50


def test_reader_never_observes_a_partial_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import adlife.city.workbench_city_registry as registry_module

    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    writer = WorkspaceCityRegistry(workspace)
    reader = WorkspaceCityRegistry(workspace)
    linked = threading.Event()
    release = threading.Event()
    real_link = registry_module._link_stage

    def pausing_link(*args: object, **kwargs: object) -> None:
        real_link(*args, **kwargs)
        linked.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(registry_module, "_link_stage", pausing_link)
    write_failure: list[BaseException] = []
    read_result: list[tuple[str, ...]] = []

    def publish() -> None:
        try:
            writer.register(_document("atomic-city"), forbidden_city_ids=frozenset())
        except BaseException as error:  # pragma: no cover - asserted below
            write_failure.append(error)

    def discover() -> None:
        read_result.append(tuple(pack.city_id for pack in reader.discover()))

    writer_thread = threading.Thread(target=publish)
    writer_thread.start()
    assert linked.wait(timeout=10)
    reader_thread = threading.Thread(target=discover)
    reader_thread.start()
    assert reader_thread.is_alive()
    release.set()
    writer_thread.join(timeout=10)
    reader_thread.join(timeout=10)

    assert write_failure == []
    assert read_result == [("atomic-city",)]
