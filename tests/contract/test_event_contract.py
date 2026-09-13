from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.results import RunManifest, SimulationResult


def manifest() -> RunManifest:
    return RunManifest(
        run_id="run-contract",
        scenario_id="scenario-contract",
        scenario_hash="a" * 64,
        seed=42,
        package_version="0.1.0",
        git_sha="b" * 40,
        lockfile_sha256="c" * 64,
        provider="mock",
        model_id="mock-v1",
        prompt_version="cognition-v1",
        prompt_hash="d" * 64,
        platform="windows-x86_64-python-3.12",
    )


def noticed_event() -> DomainEvent:
    return DomainEvent(
        event_id="run-contract:event-00000002",
        run_id="run-contract",
        simulated_minute=15,
        sequence=2,
        event_type=EventType.CAMPAIGN_NOTICED,
        agent_id="person-001",
        campaign_id="campaign-phone",
        channel="mobile-feed",
        payload={"notice_probability": 0.75},
        source=EventSource.MOCK,
        model_id="mock-v1",
        prompt_hash="a" * 64,
        caused_by_event_ids=("run-contract:event-00000001",),
    )


def test_event_type_wire_values_are_complete() -> None:
    assert {event_type.value for event_type in EventType} == {
        "run.started",
        "agent.activity_changed",
        "agent.location_changed",
        "campaign.eligible",
        "campaign.impression",
        "campaign.noticed",
        "campaign.ignored",
        "cognition.requested",
        "cognition.completed",
        "cognition.fallback",
        "memory.created",
        "social.shared",
        "social.received",
        "agent.state_updated",
        "day.reflected",
        "run.completed",
        "run.failed",
    }


def test_event_source_wire_values_are_complete() -> None:
    assert {source.value for source in EventSource} == {
        "rule",
        "mock",
        "local-llm",
        "remote-llm",
        "replay",
        "fallback",
    }


def test_domain_event_round_trip_preserves_causal_data() -> None:
    event = noticed_event()

    restored = DomainEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    assert restored.caused_by_event_ids == ("run-contract:event-00000001",)
    assert restored.model_dump(mode="json")["event_type"] == "campaign.noticed"


def test_domain_event_is_frozen() -> None:
    event = noticed_event()

    with pytest.raises(ValidationError, match="Instance is frozen"):
        event.sequence = 3


def test_domain_event_payload_mapping_is_frozen() -> None:
    event = noticed_event()

    assert event.payload["notice_probability"] == 0.75
    with pytest.raises(TypeError):
        event.payload["notice_probability"] = 0.5


def test_domain_event_payload_nested_containers_are_frozen() -> None:
    data = noticed_event().model_dump(mode="python")
    data["payload"] = {"assessment": {"labels": ["noticed", "relevant"]}}

    event = DomainEvent.model_validate(data)
    assessment = event.payload["assessment"]

    assert isinstance(assessment, Mapping)
    with pytest.raises(TypeError):
        assessment["outcome"] = "positive"
    assert assessment["labels"] == ("noticed", "relevant")


def test_domain_event_payload_is_detached_from_input_containers() -> None:
    payload = {"assessment": {"labels": ["noticed"]}}
    data = noticed_event().model_dump(mode="python")
    data["payload"] = payload
    event = DomainEvent.model_validate(data)

    payload["assessment"]["labels"].append("changed")

    assert event.model_dump(mode="json")["payload"] == {"assessment": {"labels": ["noticed"]}}


@pytest.mark.parametrize(
    "payload",
    [
        {"unsupported": object()},
        {"not_a_number": float("nan")},
        {"infinite": float("inf")},
        {1: "non-string key"},
    ],
)
def test_domain_event_rejects_non_json_payload_values(payload: object) -> None:
    data = noticed_event().model_dump(mode="python")
    data["payload"] = payload

    with pytest.raises(ValidationError):
        DomainEvent.model_validate(data)


def test_domain_event_rejects_cyclic_payload() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    data = noticed_event().model_dump(mode="python")
    data["payload"] = cyclic

    with pytest.raises(ValidationError):
        DomainEvent.model_validate(data)


@pytest.mark.parametrize(
    "payload",
    [{"message": "\ud800"}, {"\ud800": "message"}],
    ids=["value", "key"],
)
def test_domain_event_rejects_lone_surrogate_payload_string(
    payload: dict[str, str],
) -> None:
    data = noticed_event().model_dump(mode="python")
    data["payload"] = payload

    with pytest.raises(ValidationError):
        DomainEvent.model_validate(data)


def test_domain_event_accepts_serializable_persian_payload() -> None:
    data = noticed_event().model_dump(mode="python")
    data["payload"] = {
        "message": "\u0633\u0644\u0627\u0645 \u062f\u0646\u06cc\u0627",
        "labels": ["\u0622\u06af\u0627\u0647", "\u06a9\u0646\u062c\u06a9\u0627\u0648"],
    }

    event = DomainEvent.model_validate(data)
    restored = DomainEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    assert restored.payload["message"] == "\u0633\u0644\u0627\u0645 \u062f\u0646\u06cc\u0627"


def test_nested_domain_event_payload_round_trips_as_json() -> None:
    data = noticed_event().model_dump(mode="python")
    data["payload"] = {
        "assessment": {
            "labels": ["noticed", "relevant"],
            "score": 0.75,
            "accepted": True,
            "reason": None,
        }
    }
    event = DomainEvent.model_validate(data)

    restored = DomainEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    assert restored.model_dump(mode="json")["payload"] == data["payload"]


def test_domain_event_copy_accepts_its_frozen_payload() -> None:
    event = noticed_event()

    copied = event.model_copy(update={"payload": event.payload})

    assert copied == event


def test_domain_event_copy_rejects_datetime_payload() -> None:
    event = noticed_event()

    with pytest.raises(ValidationError):
        event.model_copy(update={"payload": {"observed_at": datetime(2026, 9, 13, tzinfo=UTC)}})


@pytest.mark.parametrize(("field", "value"), [("simulated_minute", -1), ("sequence", -1)])
def test_domain_event_rejects_negative_ordering_values(field: str, value: int) -> None:
    data = {
        "event_id": "run-contract:event-00000001",
        "run_id": "run-contract",
        "simulated_minute": 0,
        "sequence": 1,
        "event_type": EventType.RUN_STARTED,
        "source": EventSource.RULE,
    }
    data[field] = value

    with pytest.raises(ValidationError):
        DomainEvent.model_validate(data)


def test_domain_event_rejects_duplicate_causes() -> None:
    with pytest.raises(ValidationError, match="causal event identifiers must be unique"):
        DomainEvent(
            event_id="run-contract:event-00000002",
            run_id="run-contract",
            simulated_minute=15,
            sequence=2,
            event_type=EventType.STATE_UPDATED,
            source=EventSource.RULE,
            caused_by_event_ids=(
                "run-contract:event-00000001",
                "run-contract:event-00000001",
            ),
        )


def test_domain_event_cannot_cause_itself() -> None:
    with pytest.raises(ValidationError, match="cannot cause itself"):
        DomainEvent(
            event_id="run-contract:event-00000002",
            run_id="run-contract",
            simulated_minute=15,
            sequence=2,
            event_type=EventType.STATE_UPDATED,
            source=EventSource.RULE,
            caused_by_event_ids=("run-contract:event-00000002",),
        )


def test_run_manifest_round_trips_with_schema_version() -> None:
    value = manifest()

    restored = RunManifest.model_validate_json(value.model_dump_json())

    assert restored == value
    assert restored.schema_version == 1


def test_run_manifest_is_frozen() -> None:
    value = manifest()

    with pytest.raises(ValidationError, match="Instance is frozen"):
        value.seed = 43


def test_run_manifest_accepts_explicit_uncommitted_git_state() -> None:
    data = manifest().model_dump(mode="python")
    data["git_sha"] = "uncommitted"

    assert RunManifest.model_validate(data).git_sha == "uncommitted"


@pytest.mark.parametrize("provider", ["rules", "mock", "local", "remote", "replay"])
@pytest.mark.parametrize("field", ["model_id", "prompt_version", "prompt_hash"])
def test_run_manifest_requires_complete_provider_fingerprint(
    provider: str,
    field: str,
) -> None:
    data = manifest().model_dump(mode="python")
    data["provider"] = provider
    data.pop(field)

    with pytest.raises(ValidationError):
        RunManifest.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("model_id", ""), ("prompt_version", ""), ("prompt_hash", "")],
)
def test_run_manifest_rejects_empty_provider_fingerprint(
    field: str,
    value: str,
) -> None:
    data = manifest().model_dump(mode="python")
    data[field] = value

    with pytest.raises(ValidationError):
        RunManifest.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("scenario_hash", "short"),
        ("seed", -1),
        ("git_sha", "not-a-git-sha"),
        ("lockfile_sha256", "short"),
        ("prompt_hash", "short"),
    ],
)
def test_run_manifest_rejects_invalid_reproducibility_values(
    field: str,
    value: object,
) -> None:
    data = manifest().model_dump(mode="python")
    data[field] = value

    with pytest.raises(ValidationError):
        RunManifest.model_validate(data)


def test_simulation_result_round_trips() -> None:
    result = SimulationResult(
        run_id="run-contract",
        status="completed",
        final_minute=1440,
        event_count=25,
        metrics={"reach": 1.0, "notice_rate": 0.5},
    )

    restored = SimulationResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.schema_version == 1


def test_simulation_result_metrics_mapping_is_frozen() -> None:
    result = SimulationResult(
        run_id="run-contract",
        status="completed",
        final_minute=1440,
        event_count=25,
        metrics={"reach": 1.0},
    )

    assert result.metrics["reach"] == 1.0
    with pytest.raises(TypeError):
        result.metrics["reach"] = 0.5


def test_simulation_result_copy_accepts_its_frozen_metrics() -> None:
    result = SimulationResult(
        run_id="run-contract",
        status="completed",
        final_minute=1440,
        event_count=25,
        metrics={"reach": 1.0},
    )

    copied = result.model_copy(update={"metrics": result.metrics})

    assert copied == result


def test_simulation_result_copy_rejects_integer_metric_keys() -> None:
    result = SimulationResult(
        run_id="run-contract",
        status="completed",
        final_minute=1440,
        event_count=25,
        metrics={"reach": 1.0},
    )

    with pytest.raises(ValidationError):
        result.model_copy(update={"metrics": {1: 0.5}})


@pytest.mark.parametrize("metric", [float("nan"), float("inf"), float("-inf")])
def test_simulation_result_rejects_non_json_metrics(metric: float) -> None:
    with pytest.raises(ValidationError):
        SimulationResult(
            run_id="run-contract",
            status="completed",
            final_minute=1440,
            event_count=25,
            metrics={"reach": metric},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("final_minute", 10081),
        ("event_count", -1),
    ],
)
def test_simulation_result_rejects_invalid_bounds(field: str, value: int) -> None:
    data = {
        "run_id": "run-contract",
        "status": "completed",
        "final_minute": 1440,
        "event_count": 25,
        "metrics": {},
    }
    data[field] = value

    with pytest.raises(ValidationError):
        SimulationResult.model_validate(data)


def test_failed_result_requires_failure_reason() -> None:
    with pytest.raises(ValidationError, match="failed result requires failure_reason"):
        SimulationResult(
            run_id="run-contract",
            status="failed",
            final_minute=15,
            event_count=2,
        )
