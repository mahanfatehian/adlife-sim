import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from adlife.core.domain.campaign import (
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Price,
    TimeWindow,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Route, RoutineBlock, World, Zone
from adlife.core.ports.cognition import (
    CognitionRequest,
    ProviderMetadata,
    SamplingSettings,
)
from adlife.core.simulation.decision import RuleResponse, evaluate_rule_response
from adlife.core.simulation.engine import canonical_sha256


@pytest.fixture
def valid_profile() -> PersonProfile:
    return PersonProfile(
        agent_id="person-001",
        display_name="Arman 001",
        fictional=True,
        age=29,
        occupation="office-worker",
        income_band="middle",
        household_type="shared-apartment",
        home_zone="home-north",
        work_or_study_zone="office",
        interests=frozenset({"fitness", "technology"}),
        traits=ConsumerTraits(
            price_sensitivity=0.5,
            novelty_seeking=0.6,
            social_susceptibility=0.4,
            advertising_skepticism=0.3,
            mobile_attention=0.8,
            outdoor_attention=0.5,
            brand_loyalty=0.4,
            impulsivity=0.3,
        ),
        initial_brand_sentiment=0.1,
        routine_template="office-worker",
    )


@pytest.fixture
def valid_campaign() -> Campaign:
    return Campaign(
        campaign_id="campaign-phone",
        name="Pocket Launch",
        product_name="Fictional Phone",
        product_category="consumer-electronics",
        message="A fictional phone designed for a calmer daily routine.",
        call_to_action="Explore the fictional product",
        price=Price(amount=850.0, currency="USD"),
        category_reference_price=1000.0,
        target_interests=frozenset({"technology"}),
        start_minute=0,
        end_minute=1440,
        placements=(
            PhonePlacement(
                channel="mobile-feed",
                active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=1320),),
                frequency_cap=3,
                visibility=0.8,
            ),
        ),
        creative_features=CreativeFeatures(
            description="A fictional handset on a clean white background.",
            visual_style="minimal-product",
            dominant_colors=("white", "green"),
            visible_text=("Fictional Phone",),
            contains_people=False,
        ),
    )


@pytest.fixture
def consumer_state(valid_profile: PersonProfile) -> ConsumerState:
    return ConsumerState(
        agent_id=valid_profile.agent_id,
        location="home-north",
        activity="sleep",
        mood=0.0,
        fatigue=0.1,
        brand_sentiment=valid_profile.initial_brand_sentiment,
        recall_strength=0.0,
        purchase_intention=0.0,
        cognition_budget_remaining=6,
    )


@pytest.fixture
def valid_scenario(
    valid_profile: PersonProfile,
    valid_campaign: Campaign,
    consumer_state: ConsumerState,
) -> Scenario:
    world = World(
        world_id="world-default",
        zones=(
            Zone(zone_id="home-north", name="North Home", kind="home"),
            Zone(zone_id="office", name="Office", kind="work"),
            Zone(zone_id="highway-north", name="North Highway", kind="highway"),
            Zone(zone_id="online", name="Online", kind="online"),
        ),
        routes=(
            Route(
                route_id="highway-north",
                source_zone="home-north",
                target_zone="office",
                transit_zone="highway-north",
                travel_minutes=30,
            ),
        ),
    )
    return Scenario(
        scenario_id="scenario-contract",
        name="Contract fixture",
        days=1,
        tick_minutes=15,
        world=world,
        population=(valid_profile,),
        initial_states=(consumer_state,),
        routine_blocks=(
            RoutineBlock(
                template_id="office-worker",
                day_type="weekday",
                start_minute_of_day=0,
                end_minute_of_day=1440,
                activity="sleep",
                zone_id="home-north",
            ),
        ),
        campaigns=(valid_campaign,),
    )


@pytest.fixture
def cognition_persona(valid_profile: PersonProfile) -> dict[str, object]:
    """A minimized fictional persona projection, as a prompt may carry it."""
    return {
        "age_band": "25-34",
        "household_type": valid_profile.household_type,
        "interests": sorted(valid_profile.interests),
        "occupation": valid_profile.occupation,
    }


@pytest.fixture
def cognition_campaign(valid_campaign: Campaign) -> dict[str, object]:
    """A structured campaign description, as a prompt may carry it."""
    return {
        "call_to_action": valid_campaign.call_to_action,
        "campaign_id": valid_campaign.campaign_id,
        "message": valid_campaign.message,
        "product_category": valid_campaign.product_category,
        "product_name": valid_campaign.product_name,
    }


@pytest.fixture
def cognition_request(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
) -> CognitionRequest:
    return CognitionRequest(
        request_id="run-demo:event-00000007",
        run_id="run-demo",
        simulated_minute=480,
        agent_id="person-001",
        fictional_persona=cognition_persona,
        activity="commute",
        mood=0.2,
        relevant_memories=("Noticed a fictional phone advertisement yesterday.",),
        campaign=cognition_campaign,
        channel="mobile-feed",
        exposure_count=2,
        creative_sha256="a" * 64,
    )


@pytest.fixture
def sampling_settings() -> SamplingSettings:
    return SamplingSettings()


@pytest.fixture
def provider_metadata(sampling_settings: SamplingSettings) -> ProviderMetadata:
    return ProviderMetadata(
        kind="mock",
        model_id="mock-v1",
        sampling=sampling_settings,
        prompt_sha256="b" * 64,
    )


@pytest.fixture
def rule_response(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> RuleResponse:
    return evaluate_rule_response(
        valid_profile,
        consumer_state,
        valid_campaign,
        valid_campaign.placements[0],
    )


@pytest.fixture
def run_manifest(valid_scenario: Scenario) -> RunManifest:
    """A complete manifest whose scenario digest really addresses ``valid_scenario``."""
    return RunManifest(
        run_id="run-storage",
        scenario_id=valid_scenario.scenario_id,
        scenario_hash=canonical_sha256(valid_scenario),
        seed=42,
        package_version="0.1.0",
        git_sha="uncommitted",
        lockfile_sha256="c" * 64,
        provider="rules",
        model_id="rules-v1",
        prompt_version="cognition-v1",
        prompt_hash="d" * 64,
        platform="test-platform-x86_64",
    )


@pytest.fixture
def event_factory(run_manifest: RunManifest) -> Callable[..., DomainEvent]:
    """Build a well-formed event for the manifest's run at a chosen sequence."""

    def make(
        sequence: int,
        *,
        run_id: str | None = None,
        event_type: EventType = EventType.STATE_UPDATED,
        simulated_minute: int = 0,
        payload: Mapping[str, object] | None = None,
        source: EventSource = EventSource.RULE,
        caused_by_event_ids: tuple[str, ...] = (),
        agent_id: str | None = "person-001",
        campaign_id: str | None = None,
        channel: str | None = None,
        model_id: str | None = None,
        event_id: str | None = None,
    ) -> DomainEvent:
        resolved_run_id = run_manifest.run_id if run_id is None else run_id
        return DomainEvent(
            event_id=(f"{resolved_run_id}:event-{sequence:08d}" if event_id is None else event_id),
            run_id=resolved_run_id,
            simulated_minute=simulated_minute,
            sequence=sequence,
            event_type=event_type,
            agent_id=agent_id,
            campaign_id=campaign_id,
            channel=channel,
            payload={} if payload is None else payload,
            source=source,
            model_id=model_id,
            caused_by_event_ids=caused_by_event_ids,
        )

    return make


@pytest.fixture
def failing_connection(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Arm the NEXT database connection to fail after a chosen number of statements.

    This is how an interrupted write is reproduced without killing the interpreter: the
    real connection does real work until the armed budget runs out and then raises the
    error SQLite raises for a failing device. Only one connection is affected, so the
    assertion that follows reads the artifact through an ordinary connection.
    """
    from adlife.adapters.storage import sqlite_store

    real_connect = sqlite_store.connect_to_database
    armed: dict[str, int | bool] = {"active": False, "budget": 0}

    class _FailingConnection:
        def __init__(self, wrapped: sqlite3.Connection, budget: int) -> None:
            self._wrapped = wrapped
            self._remaining = budget

        def _spend(self) -> None:
            if self._remaining <= 0:
                raise sqlite3.OperationalError("disk I/O error")
            self._remaining -= 1

        def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
            self._spend()
            return self._wrapped.execute(sql, parameters)  # type: ignore[arg-type]

        def executemany(self, sql: str, parameters: object) -> sqlite3.Cursor:
            self._spend()
            return self._wrapped.executemany(sql, parameters)  # type: ignore[arg-type]

        def __getattr__(self, name: str) -> object:
            return getattr(self._wrapped, name)

    def connect(path: Path) -> sqlite3.Connection:
        connection = real_connect(path)
        if armed["active"]:
            armed["active"] = False
            return cast(sqlite3.Connection, _FailingConnection(connection, int(armed["budget"])))
        return connection

    def arm(statements: int) -> None:
        armed["active"] = True
        armed["budget"] = statements

    monkeypatch.setattr(sqlite_store, "connect_to_database", connect)
    return arm
