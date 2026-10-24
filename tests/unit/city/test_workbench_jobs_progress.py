"""Live bounded-cognition progress is monotonic, thread-safe, bounded, and final."""

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.adapters.cognition.city_service import CityCognitionCancellation
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_SELECTION_LIMITS,
    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
    CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION,
)
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_run_preparation import _validated, _validated_v4
from tests.unit.city.test_workbench_jobs import (
    TERMINAL,
    _await_terminal,
    _GatedCognitionRepositoryV2,
    _schema4_validated,
)
from tests.unit.cognition.test_city_cache_v2 import _cache_case, _case, _record
from tests.unit.cognition.test_city_service_v2 import _Provider, _valid

PROVIDER_REVISION = "c" * 64
TIMESTAMP = "2026-10-20T00:00:00.000000Z"
CANCELLABLE = {
    "queued",
    "evaluating",
    "simulating",
    "selecting-explanations",
    "requesting-explanations",
    "planning-cognition",
    "requesting-cognition",
    "applying-cognition",
}
V2 = CITY_COGNITION_SELECTION_LIMITS[2]
CURRENT = CITY_COGNITION_SELECTION_LIMITS[CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION]
# Every version's widest per-run selection and in-flight ceiling bound a progress document.
MAX_TOTAL = max(
    limits.selected_requests_per_run for limits in CITY_COGNITION_SELECTION_LIMITS.values()
)
MAX_CONCURRENCY = max(
    limits.concurrency_limit(mode)
    for limits in CITY_COGNITION_SELECTION_LIMITS.values()
    for mode in ("local", "remote")
)


def _jobs():
    return importlib.import_module("adlife.city.workbench_jobs")


def _runs():
    return importlib.import_module("adlife.city.workbench_runs")


def _service():
    return importlib.import_module("adlife.adapters.cognition.city_service_v2")


# --- the thread-safe counter -------------------------------------------------------------


def _progress(
    resolved: int, total: int, concurrency_limit: int = V2.concurrency_limit("local")
) -> dict[str, int]:
    """One public progress document; by default with a V2 local run's in-flight ceiling."""
    return {"resolved": resolved, "total": total, "concurrency_limit": concurrency_limit}


def test_counter_counts_each_terminal_outcome_once_and_never_past_its_total() -> None:
    jobs = _jobs()
    remote = CURRENT.concurrency_limit("remote")
    counter = jobs.WorkbenchCognitionProgressCounter(3, concurrency_limit=remote)
    assert counter.snapshot().model_dump(mode="json") == _progress(0, 3, remote)
    counter.advance()
    counter.advance()
    assert counter.snapshot() == jobs.WorkbenchCognitionProgress(
        resolved=2, total=3, concurrency_limit=remote
    )
    for _ in range(5):
        counter.advance()
    assert counter.snapshot().model_dump(mode="json") == _progress(3, 3, remote)


def test_counter_completion_is_final_and_later_reports_change_nothing() -> None:
    counter = _jobs().WorkbenchCognitionProgressCounter(4)
    counter.advance()
    counter.complete()
    counter.advance()
    counter.complete()
    assert counter.snapshot().model_dump(mode="json") == _progress(4, 4)


def test_zero_total_counter_is_already_final() -> None:
    counter = _jobs().WorkbenchCognitionProgressCounter(0)
    counter.advance()
    assert counter.snapshot().model_dump(mode="json") == _progress(0, 0)


@pytest.mark.parametrize("total", [-1, MAX_TOTAL + 1, True, 1.0, "3", None])
def test_counter_refuses_totals_outside_the_frozen_funding_range(total: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _jobs().WorkbenchCognitionProgressCounter(total)


@pytest.mark.parametrize("concurrency_limit", [0, MAX_CONCURRENCY + 1, True, 4.0, "4", None])
def test_counter_refuses_concurrency_outside_the_frozen_range(concurrency_limit: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _jobs().WorkbenchCognitionProgressCounter(3, concurrency_limit=concurrency_limit)


def test_counter_accepts_the_widest_frozen_run() -> None:
    counter = _jobs().WorkbenchCognitionProgressCounter(
        MAX_TOTAL, concurrency_limit=MAX_CONCURRENCY
    )
    counter.complete()
    assert counter.snapshot().model_dump(mode="json") == _progress(
        MAX_TOTAL, MAX_TOTAL, MAX_CONCURRENCY
    )


def test_concurrent_advances_never_lose_an_answer_and_snapshots_never_regress() -> None:
    counter = _jobs().WorkbenchCognitionProgressCounter(60)
    writers = 6
    start = threading.Barrier(writers + 3)
    done = threading.Event()
    observed: list[list[int]] = [[] for _ in range(3)]

    def advance() -> None:
        start.wait(5)
        for _ in range(10):
            counter.advance()

    def watch(values: list[int]) -> None:
        start.wait(5)
        while not done.is_set():
            values.append(counter.snapshot().resolved)
        values.append(counter.snapshot().resolved)

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=advance) for _ in range(writers)]
        watchers = [threading.Thread(target=watch, args=(values,)) for values in observed]
        for thread in (*threads, *watchers):
            thread.start()
        for thread in threads:
            thread.join(10)
        done.set()
        for thread in watchers:
            thread.join(10)
    finally:
        sys.setswitchinterval(interval)

    # Exactly 60 reports from six threads reach exactly 60, and no reader saw a regression.
    assert counter.snapshot().model_dump(mode="json") == _progress(60, 60)
    for values in observed:
        assert values == sorted(values)
        assert values[-1] == 60
        assert all(0 <= value <= 60 for value in values)


@pytest.mark.parametrize("operation", ["advance", "complete", "snapshot"])
def test_every_counter_operation_waits_for_the_counter_lock(operation: str) -> None:
    counter = _jobs().WorkbenchCognitionProgressCounter(2)
    finished = threading.Event()

    def run() -> None:
        getattr(counter, operation)()
        finished.set()

    # Holding the lock from this thread proves a reader or writer cannot interleave.
    counter._lock.acquire()
    try:
        worker = threading.Thread(target=run)
        worker.start()
        assert not finished.wait(0.1)
    finally:
        counter._lock.release()
    worker.join(5)
    assert finished.is_set()


# --- the public job JSON contract ---------------------------------------------------------


def test_progress_document_is_an_exact_bounded_pair() -> None:
    jobs = _jobs()
    # The resolved/total pair carries the run's frozen in-flight ceiling for the wait estimate.
    remote = CURRENT.concurrency_limit("remote")
    progress = jobs.WorkbenchCognitionProgress(resolved=3, total=10, concurrency_limit=remote)
    assert progress.model_dump_json() == f'{{"resolved":3,"total":10,"concurrency_limit":{remote}}}'
    widest = _progress(MAX_TOTAL, MAX_TOTAL, MAX_CONCURRENCY)
    assert jobs.WorkbenchCognitionProgress.model_validate(widest).model_dump() == widest
    valid = _progress(1, 3)
    for update in (
        {"resolved": 4},
        {"resolved": -1},
        {"total": MAX_TOTAL + 1},
        {"resolved": True},
        {"resolved": 1.0},
        {"resolved": "1"},
        {"concurrency_limit": 0},
        {"concurrency_limit": MAX_CONCURRENCY + 1},
        {"concurrency_limit": True},
        {"concurrency_limit": 4.0},
        {"percent": 33},
    ):
        with pytest.raises(ValidationError):
            jobs.WorkbenchCognitionProgress.model_validate(valid | update)
    for missing in valid:
        with pytest.raises(ValidationError):
            jobs.WorkbenchCognitionProgress.model_validate(
                {name: value for name, value in valid.items() if name != missing}
            )
    with pytest.raises(ValidationError, match="frozen"):
        progress.resolved = 4


_V1_SETTINGS: dict[str, object] = {
    "workbench_input_schema_version": 1,
    "run_schema_version": 7,
    "city_id": "fictional-grid-v2",
    "scenario_id": "launch-study",
    "campaign_id": "fictional-launch",
    "seed": "42",
    "agent_count": 1,
    "days": 1,
    "response_mode": "deterministic-rules",
}
_RULES_SETTINGS: dict[str, object] = {
    **_V1_SETTINGS,
    "workbench_input_schema_version": 4,
    "run_schema_version": 10,
    "effect_scope": "bounded-signals-v1",
    "cognition_mode": "rules",
    "funded_response_limit": 60,
    "aggregate_output_token_ceiling": 0,
}
_LOCAL_SETTINGS: dict[str, object] = {
    **_RULES_SETTINGS,
    "cognition_mode": "local",
    "provider_profile_id": "provider-main",
    "provider_revision_sha256": PROVIDER_REVISION,
    "funded_response_limit": 10,
    "aggregate_output_token_ceiling": 10 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
}
# A new online run of the current version asks about every noticed ad, up to its cap.
_ALL_NOTICED = CURRENT.selected_requests_per_run
_REMOTE_CONCURRENCY = CURRENT.concurrency_limit("remote")
_REMOTE_SETTINGS: dict[str, object] = {
    **_LOCAL_SETTINGS,
    "cognition_mode": "remote",
    "funded_response_limit": _ALL_NOTICED,
    "aggregate_output_token_ceiling": CURRENT.aggregate_output_token_ceiling(
        "remote", _ALL_NOTICED
    ),
}
_SETTINGS = {
    "v1": _V1_SETTINGS,
    "rules": _RULES_SETTINGS,
    "local": _LOCAL_SETTINGS,
    "remote": _REMOTE_SETTINGS,
}


def _summary(mode: str) -> Any:
    common: dict[str, Any] = {
        "run_id": "progress-view",
        "run_schema_version": 10,
        "workbench_input_schema_version": 4,
        "city_id": "fictional-grid-v2",
        "city_schema_version": 2,
        "city_sha256": "a" * 64,
        "scenario_id": "launch-study",
        "scenario_sha256": "b" * 64,
        "campaign_ids": ("fictional-launch",),
        "channels": ("mobile-feed",),
        "seed": "42",
        "days": 1,
        "agent_count": 1,
        "frame_count": 1_440,
        "opportunity_count": 12,
        "impression_count": 12,
        "effect_scope": "bounded-signals-v1",
        "mock_count": 0,
        "unselected_response_count": 0,
        "uncertain_result_count": 0,
        "safety_neutralized_result_count": 0,
        "inspector_url": "/runs/progress-view",
    }
    if mode == "rules":
        return _runs().WorkbenchRunSummary(
            **common,
            noticed_count=5,
            response_count=5,
            cognition_mode="rules",
            provider_profile_id=None,
            provider_revision_sha256=None,
            funded_response_limit=60,
            aggregate_output_token_ceiling=0,
            eligible_response_count=5,
            selected_request_count=5,
            funded_request_count=5,
            budget_skipped_count=0,
            applied_response_count=5,
            rules_count=5,
            provider_count=0,
            cache_count=0,
            fallback_count=0,
            neutralized_response_count=5,
        )
    return _runs().WorkbenchRunSummary(
        **common,
        noticed_count=12,
        response_count=12,
        cognition_mode="local",
        provider_profile_id="provider-main",
        provider_revision_sha256=PROVIDER_REVISION,
        funded_response_limit=10,
        aggregate_output_token_ceiling=10 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
        eligible_response_count=12,
        selected_request_count=12,
        funded_request_count=10,
        budget_skipped_count=2,
        applied_response_count=12,
        rules_count=0,
        provider_count=7,
        cache_count=2,
        fallback_count=1,
        neutralized_response_count=3,
    )


def _view(settings: str, phase: str, progress: object) -> Any:
    cancelled = phase == "cancelled"
    return _jobs().WorkbenchJobView.model_validate(
        {
            "job_id": "job-" + "a" * 32,
            "run_id": "progress-view",
            "accepted_settings": _SETTINGS[settings],
            "phase": phase,
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
            "cancellation_requested": cancelled,
            "can_cancel": phase in CANCELLABLE and not cancelled,
            "error": (
                {
                    "code": "evaluation-failed",
                    "message": "The deterministic city evaluation failed.",
                }
                if phase == "failed"
                else None
            ),
            "result": _summary(settings) if phase == "completed" else None,
            "cognition_progress": progress,
        }
    )


@pytest.mark.parametrize(
    ("settings", "phase", "progress"),
    [
        ("v1", "queued", None),
        ("v1", "evaluating", None),
        ("local", "queued", None),
        ("local", "planning-cognition", None),
        ("local", "requesting-cognition", _progress(0, 10)),
        ("local", "requesting-cognition", _progress(3, 10)),
        ("local", "requesting-cognition", _progress(0, 6)),
        ("local", "applying-cognition", _progress(10, 10)),
        ("local", "persisting", _progress(10, 10)),
        ("local", "verifying", _progress(10, 10)),
        ("local", "completed", _progress(10, 10)),
        ("local", "failed", None),
        ("local", "failed", _progress(3, 10)),
        ("local", "cancelled", None),
        ("local", "cancelled", _progress(3, 10)),
        ("rules", "requesting-cognition", _progress(0, 0)),
        ("rules", "completed", _progress(0, 0)),
        ("remote", "requesting-cognition", _progress(0, _ALL_NOTICED, _REMOTE_CONCURRENCY)),
        (
            "remote",
            "applying-cognition",
            _progress(_ALL_NOTICED, _ALL_NOTICED, _REMOTE_CONCURRENCY),
        ),
    ],
)
def test_job_view_accepts_progress_only_where_the_phase_contract_allows(
    settings: str, phase: str, progress: dict[str, int] | None
) -> None:
    view = _view(settings, phase, progress)
    assert view.model_dump(mode="json")["cognition_progress"] == progress


@pytest.mark.parametrize(
    ("settings", "phase", "progress", "message"),
    [
        ("v1", "queued", _progress(0, 0), "pre-schema-4"),
        ("v1", "evaluating", _progress(0, 0), "pre-schema-4"),
        ("local", "queued", _progress(0, 10), "precede planning"),
        ("local", "planning-cognition", _progress(0, 10), "precede planning"),
        ("local", "requesting-cognition", None, "requires progress"),
        ("local", "applying-cognition", None, "requires progress"),
        ("local", "completed", None, "requires progress"),
        ("local", "applying-cognition", _progress(9, 10), "final"),
        ("local", "persisting", _progress(9, 10), "final"),
        ("local", "verifying", _progress(9, 10), "final"),
        ("local", "completed", _progress(9, 10), "final"),
        ("local", "requesting-cognition", _progress(0, 11), "funding"),
        ("rules", "requesting-cognition", _progress(0, 1), "funding"),
        ("local", "completed", _progress(9, 9), "funded requests"),
    ],
)
def test_job_view_refuses_progress_that_contradicts_its_phase_or_funding(
    settings: str, phase: str, progress: dict[str, int] | None, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _view(settings, phase, progress)


def test_constructed_progress_is_revalidated_inside_a_job_view() -> None:
    forged = _jobs().WorkbenchCognitionProgress.model_construct(**_progress(11, 10))
    with pytest.raises(ValidationError, match="more requests than it funds"):
        _view("local", "requesting-cognition", forged)


# --- the cognition service hook ------------------------------------------------------------


def _funded(frozen: Any) -> int:
    return sum(1 for reservation in frozen.reservations if reservation.reserved_output_tokens > 0)


async def _until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("the expected progress report did not arrive")
        await asyncio.sleep(0.001)


async def _resolve_with(
    case: Any,
    *,
    provider: Any = None,
    cache: Any = None,
    progress: Any = None,
) -> Any:
    _, baseline, frozen, requests = case
    return await (
        _service()
        .CityCognitionServiceV2(cache=cache)
        .resolve_many(
            frozen,
            requests,
            baseline_evaluation=baseline,
            provider=provider,
            cancellation=CityCognitionCancellation(),
            progress=progress,
        )
    )


async def test_service_reports_each_funded_answer_once_as_it_arrives() -> None:
    case = _case(agent_count=4, limit=10)
    frozen, requests = case[2], case[3]
    funded = _funded(frozen)
    assert 0 < funded < len(requests)
    release = {request.request_id: asyncio.Event() for request in requests}
    answered = 0
    reports: list[int] = []

    async def slow(request: Any) -> Any:
        nonlocal answered
        await release[request.request_id].wait()
        answered += 1
        return _valid(request)

    provider = _Provider(slow)
    task = asyncio.create_task(
        _resolve_with(case, provider=provider, progress=lambda: reports.append(answered))
    )
    await asyncio.sleep(0.02)
    assert reports == []
    # Requests start in the service's interleaved order, so answer each one as it is sent.
    for count in range(1, funded + 1):
        await _until(lambda expected=count: len(provider.calls) >= expected)
        release[provider.calls[count - 1]].set()
        await _until(lambda expected=count: len(reports) >= expected)
        assert reports == list(range(1, count + 1))
    evaluation = await asyncio.wait_for(task, 5)

    assert reports == list(range(1, funded + 1))
    assert sorted(provider.calls) == sorted(
        reservation.request_id for reservation in frozen.reservations if reservation.funded
    )
    sources = Counter(record.source for record in evaluation.records)
    assert sources == {"provider": funded, "budget-skipped": len(requests) - funded}
    # The hook only observes: the frozen terminal evidence is byte-identical without it.
    unobserved = await _resolve_with(case, provider=_Provider())
    assert canonical_json(evaluation) == canonical_json(unobserved)


async def test_cache_hits_count_as_answers_but_budget_skipped_requests_never_do(
    tmp_path: Path,
) -> None:
    cache, _ = _cache_case(tmp_path)
    case = _case(agent_count=4, limit=10)
    frozen, requests = case[2], case[3]
    for request in requests[:5]:
        record = _record(request, frozen)
        cache.put(record.key, record)
    provider = _Provider()
    reports: list[int] = []

    evaluation = await _resolve_with(
        case,
        provider=provider,
        cache=cache,
        progress=lambda: reports.append(len(provider.calls)),
    )

    funded = _funded(frozen)
    assert funded == 10 < len(requests)
    assert len(reports) == funded
    # Cached answers are counted while the funded prefix is admitted, before any request.
    assert reports[:5] == [0] * 5
    sources = Counter(record.source for record in evaluation.records)
    assert sources == {"cache": 5, "provider": 5, "budget-skipped": len(requests) - 10}


async def test_provider_failures_count_once_as_neutral_fallbacks() -> None:
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")

    async def refuse(request: Any) -> Any:
        raise errors.CityProviderAuthenticationError()

    case = _case(agent_count=4, limit=10)
    reports: list[int] = []
    evaluation = await _resolve_with(
        case, provider=_Provider(refuse), progress=lambda: reports.append(1)
    )
    assert len(reports) == _funded(case[2])
    assert Counter(record.source for record in evaluation.records)["fallback"] == len(reports)


async def test_requests_still_open_at_the_aggregate_deadline_count_as_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _service()
    case = _case(agent_count=4, limit=10)
    first = case[3][0].request_id
    real_timeout = asyncio.timeout

    def short(delay: float) -> Any:
        return real_timeout(0.05 if delay == 180 else delay)

    monkeypatch.setattr(module.asyncio, "timeout", short)

    async def answer_first_only(request: Any) -> Any:
        if request.request_id == first:
            return _valid(request)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    reports: list[int] = []
    evaluation = await _resolve_with(
        case, provider=_Provider(answer_first_only), progress=lambda: reports.append(1)
    )
    funded = _funded(case[2])
    assert len(reports) == funded
    sources = Counter(record.source for record in evaluation.records)
    assert sources["provider"] == 1
    assert sources["fallback"] == funded - 1


@pytest.mark.parametrize("mode", ["rules", "mock"])
async def test_offline_sources_never_report_model_answers(mode: str) -> None:
    reports: list[int] = []
    evaluation = await _resolve_with(
        _case(mode=mode, agent_count=4), progress=lambda: reports.append(1)
    )
    assert {record.source for record in evaluation.records} == {mode}
    assert reports == []


async def test_service_refuses_a_non_callable_hook_and_still_closes_the_provider() -> None:
    provider = _Provider()
    with pytest.raises(TypeError, match="progress"):
        await _resolve_with(_case(), provider=provider, progress="not-callable")
    assert provider.calls == []
    assert provider.close_calls == 1


# --- the job worker ------------------------------------------------------------------------


class _SlowProvider:
    """A fake V2 model whose every answer waits for one permit the test releases."""

    def __init__(self, permits: threading.Semaphore, **kwargs: object) -> None:
        self.permits = permits
        self.kwargs = kwargs
        self.calls = 0
        self.closed = False

    async def call(self, request: Any) -> Any:
        self.calls += 1
        while not self.permits.acquire(blocking=False):
            await asyncio.sleep(0.002)
        return _valid(request)

    async def repair_call(self, request: Any, *, context: Any) -> Any:
        raise AssertionError("a valid signal never needs repair")

    async def aclose(self) -> None:
        self.closed = True


def _local_job(
    tmp_path: Path,
    *,
    factory: Callable[..., Any],
    run_id: str,
    agents: int = 3,
) -> tuple[Any, Any]:
    """A checked local profile whose three agents select six funded requests in two days.

    The run is frozen directly, so it keeps the historical V2 contract and its in-flight
    ceiling.
    """
    from adlife.city.provider_compatibility import (
        WorkbenchProviderCompatibilityRecord,
        WorkbenchProviderCompatibilityRegistry,
        build_workbench_provider_compatibility_key,
    )
    from adlife.city.provider_profiles import ProviderProfileRegistry
    from tests.unit.city.test_provider_profiles import _draft

    workspace = prepare_workbench_workspace(tmp_path / "state")
    profiles = ProviderProfileRegistry(workspace)
    profile = profiles.create(_draft(profile_id="provider-main"))
    validated = _validated_v4(
        mode="local",
        limit=10,
        profile=profile,
        agents=agents,
        mutation=lambda document: document["settings"].update(run_id=run_id, days=2),
    )
    registry = WorkbenchProviderCompatibilityRegistry()
    registry.record(
        WorkbenchProviderCompatibilityRecord(
            key=build_workbench_provider_compatibility_key(
                profile, effect_scope="bounded-signals-v1"
            ),
            status="compatible",
            reason_code=None,
        )
    )
    repository = _runs().WorkbenchRunRepository(
        workspace,
        provider_registry=profiles,
        compatibility_registry=registry,
        provider_factory_v2=factory,
    )
    return repository, validated


def _await_view(
    manager: Any,
    job_id: str,
    predicate: Callable[[Any], bool],
    *,
    timeout: float = 10.0,
) -> Any:
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    while time.monotonic() < deadline:
        view = manager.get(job_id)
        if predicate(view):
            return view
        pause.wait(0.002)
    raise AssertionError(f"job {job_id} did not reach the expected progress")


def _resolved_at_least(count: int) -> Callable[[Any], bool]:
    def reached(view: Any) -> bool:
        progress = view.cognition_progress
        return progress is not None and progress.resolved >= count

    return reached


def test_slow_provider_job_counts_each_answer_from_planning_to_completion(
    tmp_path: Path,
) -> None:
    jobs = _jobs()
    permits = threading.Semaphore(0)
    providers: list[_SlowProvider] = []

    def factory(**kwargs: object) -> _SlowProvider:
        provider = _SlowProvider(permits, **kwargs)
        providers.append(provider)
        return provider

    repository, validated = _local_job(tmp_path, factory=factory, run_id="progress-v10")
    gated = _GatedCognitionRepositoryV2(repository)
    manager = jobs.CityJobManager(gated)
    try:
        submitted = manager.submit(validated)
        assert submitted.model_dump(mode="json")["cognition_progress"] is None
        assert gated.planning_entered.wait(10)
        planning = manager.get(submitted.job_id)
        assert planning.phase == "planning-cognition"
        assert planning.cognition_progress is None

        gated.allow_planning.set()
        assert gated.requesting_entered.wait(10)
        requesting = manager.get(submitted.job_id)
        assert requesting.phase == "requesting-cognition"
        assert requesting.model_dump(mode="json")["cognition_progress"] == _progress(0, 6)

        gated.allow_requesting.set()
        for answered in range(1, 7):
            permits.release()
            view = _await_view(manager, submitted.job_id, _resolved_at_least(answered))
            assert view.model_dump(mode="json")["cognition_progress"] == _progress(answered, 6)

        assert gated.applying_entered.wait(10)
        applying = manager.get(submitted.job_id)
        assert applying.phase == "applying-cognition"
        assert applying.model_dump(mode="json")["cognition_progress"] == _progress(6, 6)
        gated.release_all()
        completed = _await_terminal(manager, submitted.job_id)
        assert completed.phase == "completed"
        assert completed.model_dump(mode="json")["cognition_progress"] == _progress(6, 6)
        assert completed.result is not None
        assert completed.result.funded_request_count == 6
        assert completed.result.provider_count == 6
        assert sum(provider.calls for provider in providers) == 6
        assert all(provider.closed for provider in providers)
    finally:
        gated.release_all()
        permits.release(64)
        manager.close()


def test_status_readers_see_monotonic_bounded_progress_while_answers_arrive(
    tmp_path: Path,
) -> None:
    jobs = _jobs()
    permits = threading.Semaphore(0)
    repository, validated = _local_job(
        tmp_path,
        factory=lambda **kwargs: _SlowProvider(permits, **kwargs),
        run_id="progress-race-v10",
    )
    manager = jobs.CityJobManager(repository)
    observed: list[list[tuple[str, int | None, int | None]]] = [[] for _ in range(3)]
    stop = threading.Event()
    try:
        submitted = manager.submit(validated)

        def watch(values: list[tuple[str, int | None, int | None]]) -> None:
            # Readers poll as fast as a client could without starving the worker's lock.
            while not stop.wait(0.0005):
                view = manager.get(submitted.job_id)
                progress = view.cognition_progress
                values.append(
                    (
                        view.phase,
                        None if progress is None else progress.resolved,
                        None if progress is None else progress.total,
                    )
                )
                if view.phase in TERMINAL:
                    return

        watchers = [threading.Thread(target=watch, args=(values,)) for values in observed]
        for watcher in watchers:
            watcher.start()
        _await_view(manager, submitted.job_id, _resolved_at_least(0))
        # Answers trickle in while three status readers poll the same record.
        for _ in range(6):
            time.sleep(0.01)
            permits.release()
        terminal = _await_terminal(manager, submitted.job_id, timeout=30)
        # Each reader stops only after it has itself observed the terminal record.
        for watcher in watchers:
            watcher.join(10)
            assert not watcher.is_alive()
    finally:
        stop.set()
        permits.release(64)
        manager.close()

    assert terminal.phase == "completed"
    for values in observed:
        assert values[-1] == ("completed", 6, 6)
        counted = [resolved for _phase, resolved, _total in values if resolved is not None]
        assert counted == sorted(counted)
        assert counted[-1] == 6
        assert {total for _phase, _resolved, total in values if total is not None} == {6}
        for phase, resolved, total in values:
            if phase in {"queued", "planning-cognition"}:
                assert resolved is None and total is None
            else:
                assert resolved is not None and total is not None and resolved <= total
            if phase in {"applying-cognition", "persisting", "verifying", "completed"}:
                assert resolved == total


def test_cancelled_requesting_job_keeps_its_partial_count(tmp_path: Path) -> None:
    jobs = _jobs()
    permits = threading.Semaphore(0)
    repository, validated = _local_job(
        tmp_path,
        factory=lambda **kwargs: _SlowProvider(permits, **kwargs),
        run_id="progress-cancel-v10",
    )
    manager = jobs.CityJobManager(repository)
    try:
        submitted = manager.submit(validated)
        _await_view(manager, submitted.job_id, _resolved_at_least(0))
        permits.release()
        _await_view(manager, submitted.job_id, _resolved_at_least(1))
        requested = manager.cancel(submitted.job_id)
        assert requested.model_dump(mode="json")["cognition_progress"] == _progress(1, 6)
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "cancelled"
        assert terminal.model_dump(mode="json")["cognition_progress"] == _progress(1, 6)
    finally:
        permits.release(64)
        manager.close()


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_offline_bounded_job_reports_zero_model_requests(tmp_path: Path, mode: str) -> None:
    jobs = _jobs()
    gated = _GatedCognitionRepositoryV2(
        _runs().WorkbenchRunRepository(prepare_workbench_workspace(tmp_path / mode))
    )
    manager = jobs.CityJobManager(gated)
    try:
        # A new offline run answers every selected request itself and sends none.
        submitted = manager.submit(_schema4_validated(run_id=f"offline-progress-{mode}", mode=mode))
        expected = _progress(0, 0, CURRENT.concurrency_limit(mode))
        gated.allow_planning.set()
        assert gated.requesting_entered.wait(10)
        requesting = manager.get(submitted.job_id)
        assert requesting.model_dump(mode="json")["cognition_progress"] == expected
        gated.release_all()
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "completed"
        assert terminal.model_dump(mode="json")["cognition_progress"] == expected
        assert terminal.accepted_settings.funded_response_limit == (
            CURRENT.offline_funded_response_limit
        )
    finally:
        gated.release_all()
        manager.close()


def test_legacy_job_never_reports_cognition_progress(tmp_path: Path) -> None:
    jobs = _jobs()
    manager = jobs.CityJobManager(
        _runs().WorkbenchRunRepository(prepare_workbench_workspace(tmp_path / "state"))
    )
    try:
        submitted = manager.submit(_validated(run_id="legacy-progress", agents=1, days=2))
        assert submitted.model_dump(mode="json")["cognition_progress"] is None
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "completed"
        assert terminal.model_dump(mode="json")["cognition_progress"] is None
    finally:
        manager.close()
