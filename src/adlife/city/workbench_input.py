"""Strict, frozen inputs for the local fictional city workbench."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Final, Literal, Self, TypeAlias

from pydantic import (
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from adlife.core.domain.city import TravelDirection, _validate_public_metadata
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_MAX_AGGREGATE_OUTPUT_TOKENS,
    CITY_COGNITION_PROMPT_CONTRACTS,
    CITY_COGNITION_SELECTION_LIMITS,
    CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION,
    CityCognitionCausalContractV2,
    CityCognitionFundedResponseLimit,
    CityCognitionMaxRequestBytes,
    CityCognitionOutputTokensPerAttempt,
    CityCognitionReservationTokensPerRequest,
    CityCognitionThoughtLanguageV1,
    city_cognition_thought_languages,
)
from adlife.core.domain.identifiers import (
    WINDOWS_RESERVED_RUN_IDS,
    validate_portable_run_identifier,
)
from adlife.core.domain.json_values import MAX_JSON_DEPTH
from adlife.core.domain.person import (
    DomainModel,
    contains_provider_secret_text,
    contains_sensitive_text,
    contains_user_labelled_secret_member,
    contains_user_secret_text,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    BillboardSide,
    SpatialActiveWindow,
    SpatialActivity,
)
from adlife.core.domain.spatial_response import SpatialResponseTraits

_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_PROVIDER_PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ACTIVITY_ORDER = {"home": 0, "commute": 1, "work": 2, "leisure": 3}
CREATIVE_DISCLOSURE: Final = "Fictional creative for synthetic simulation only."
PublicText = Annotated[str, Field(min_length=1, max_length=80)]
MAX_WORKBENCH_PERSONA_BYTES: Final = 3_072
MAX_WORKBENCH_POPULATION_BYTES: Final = 98_304
MAX_WORKBENCH_INPUT_BYTES: Final = 131_072
PersonaId = Annotated[str, Field(pattern=r"^persona-(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})$")]
MbtiLabel = Literal[
    "INTJ",
    "INTP",
    "ENTJ",
    "ENTP",
    "INFJ",
    "INFP",
    "ENFJ",
    "ENFP",
    "ISTJ",
    "ISFJ",
    "ESTJ",
    "ESFJ",
    "ISTP",
    "ISFP",
    "ESTP",
    "ESFP",
]


def _safe_public_text(value: str) -> str:
    checked = _validate_public_metadata(value)
    # A person wrote this text: refuse credential shapes, not the raw-provider-body
    # heuristics that read "basic training" or "secret dream: ..." as a credential.
    if contains_user_secret_text(checked):
        raise ValueError("workbench text contains credential-shaped content")
    return checked


def _safe_identifier(value: str) -> str:
    if contains_provider_secret_text(value):
        raise ValueError("workbench identifier contains credential-shaped content")
    return value


def _no_thought_languages(value: object) -> bool:
    return value == ()


def _safe_provider_profile_id(value: str | None) -> str | None:
    if value is None:
        return None
    checked = _safe_identifier(value)
    if checked in WINDOWS_RESERVED_RUN_IDS:
        raise ValueError("provider profile identifier is reserved on Windows")
    return checked


def _unique_text_members(value: object, *, label: str) -> object:
    if isinstance(value, (list, tuple, set, frozenset)) and all(
        isinstance(item, str) for item in value
    ):
        items = tuple(value)
        if len(items) != len(set(items)):
            raise ValueError(f"{label} must be unique")
        return frozenset(items)
    return value


def _safe_interests(value: frozenset[str], *, label: str) -> frozenset[str]:
    for item in value:
        _safe_public_text(item)
    return value


class WorkbenchDailyWindow(DomainModel):
    """One half-open interval within a numbered synthetic model day."""

    day: int = Field(ge=1, le=7)
    start_minute: int = Field(ge=0, le=1_439)
    end_minute: int = Field(ge=1, le=1_440)

    @model_validator(mode="after")
    def forward_window(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("workbench active window end must be after start")
        return self


def _canonical_daily_windows(
    value: tuple[WorkbenchDailyWindow, ...],
) -> tuple[WorkbenchDailyWindow, ...]:
    windows = tuple(sorted(value, key=lambda item: (item.day, item.start_minute, item.end_minute)))
    for previous, current in pairwise(windows):
        if current.day == previous.day and current.start_minute < previous.end_minute:
            raise ValueError("workbench active windows cannot overlap")
    return windows


def _canonical_absolute_windows(
    value: tuple[SpatialActiveWindow, ...],
) -> tuple[SpatialActiveWindow, ...]:
    windows = tuple(sorted(value, key=lambda item: (item.start_minute, item.end_minute)))
    for previous, current in pairwise(windows):
        if current.start_minute < previous.end_minute:
            raise ValueError("workbench active windows cannot overlap")
    return windows


class WorkbenchCampaignDraft(DomainModel):
    campaign_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    creative_template_id: str = Field(pattern=_ID_PATTERN)
    target_interests: frozenset[PublicText] = Field(min_length=1, max_length=12)
    relative_price: float = Field(gt=0, le=100)

    @field_validator("campaign_id", "creative_template_id")
    @classmethod
    def safe_identifiers(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        return _safe_public_text(value)

    @field_validator("target_interests", mode="before")
    @classmethod
    def unique_target_interests(cls, value: object) -> object:
        return _unique_text_members(value, label="campaign target interests")

    @field_validator("target_interests")
    @classmethod
    def safe_target_interests(cls, value: frozenset[str]) -> frozenset[str]:
        return _safe_interests(value, label="campaign target interests")

    @field_serializer("target_interests", when_used="json")
    def serialize_target_interests(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class _WorkbenchPlacementDraft(DomainModel):
    active_windows: tuple[WorkbenchDailyWindow, ...] = Field(min_length=1, max_length=14)
    frequency_cap_per_agent_per_day: int = Field(ge=1, le=100)

    @field_validator("active_windows")
    @classmethod
    def canonical_windows(
        cls, value: tuple[WorkbenchDailyWindow, ...]
    ) -> tuple[WorkbenchDailyWindow, ...]:
        return _canonical_daily_windows(value)


class WorkbenchPhonePlacementDraft(_WorkbenchPlacementDraft):
    eligible_activities: tuple[SpatialActivity, ...] = Field(min_length=1, max_length=4)
    opportunity_probability_per_minute: float = Field(ge=0, le=1)

    @field_validator("eligible_activities", mode="before")
    @classmethod
    def unique_activities(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            items = tuple(value)
            if not all(isinstance(item, str) for item in items):
                return value
            if len(items) != len(set(items)):
                raise ValueError("eligible phone activities must be unique")
            return items
        return value

    @field_validator("eligible_activities")
    @classmethod
    def canonical_activities(
        cls, value: tuple[SpatialActivity, ...]
    ) -> tuple[SpatialActivity, ...]:
        return tuple(sorted(value, key=_ACTIVITY_ORDER.__getitem__))


class WorkbenchRoadsidePlacementDraft(_WorkbenchPlacementDraft):
    road_id: str = Field(pattern=_ID_PATTERN)
    travel_direction: TravelDirection
    road_fraction: float = Field(gt=0, lt=1)
    side: BillboardSide
    orientation_degrees: float = Field(ge=0, lt=360)
    max_view_distance_meters: float = Field(gt=0, le=1_000)

    @field_validator("road_id")
    @classmethod
    def safe_road_id(cls, value: str) -> str:
        return _safe_identifier(value)


class WorkbenchScenarioDraft(DomainModel):
    scenario_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    campaign: WorkbenchCampaignDraft
    phone: WorkbenchPhonePlacementDraft | None = None
    roadside: WorkbenchRoadsidePlacementDraft | None = None

    @field_validator("scenario_id")
    @classmethod
    def safe_scenario_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        return _safe_public_text(value)

    @model_validator(mode="after")
    def at_least_one_placement(self) -> Self:
        if self.phone is None and self.roadside is None:
            raise ValueError("workbench scenario requires at least one placement")
        return self


class WorkbenchInitialState(DomainModel):
    brand_sentiment: float = Field(ge=-1, le=1)
    recall_strength: float = Field(ge=0, le=1)
    purchase_intention: float = Field(ge=0, le=1)


class WorkbenchCohortDraft(DomainModel):
    interests: frozenset[PublicText] = Field(min_length=1, max_length=12)
    traits: SpatialResponseTraits
    initial_state: WorkbenchInitialState

    @field_validator("interests", mode="before")
    @classmethod
    def unique_interests(cls, value: object) -> object:
        return _unique_text_members(value, label="cohort interests")

    @field_validator("interests")
    @classmethod
    def safe_interests(cls, value: frozenset[str]) -> frozenset[str]:
        return _safe_interests(value, label="cohort interests")

    @field_serializer("interests", when_used="json")
    def serialize_interests(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


def _safe_persona_text(value: str) -> str:
    checked = _safe_public_text(value)
    if contains_sensitive_text(checked):
        raise ValueError("persona text contains a sensitive identifier or secret")
    if any(
        character in "\u2028\u2029" or "\ud800" <= character <= "\udfff" for character in checked
    ):
        raise ValueError("persona text contains a display control character")
    return checked


def _reject_structural_secrets(value: object) -> object:
    # Bound malformed Python/decoded JSON containers iteratively before invoking
    # the shared recursive screen or serializers. Valid inputs are much shallower.
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        children: Iterable[object]
        if isinstance(current, Mapping):
            children = current.values()
        elif isinstance(current, (list, tuple)):
            children = current
        else:
            continue
        if depth >= MAX_JSON_DEPTH:
            raise ValueError("workbench input exceeds its nesting limit")
        pending.extend((child, depth + 1) for child in children)
    # Every caller walks a draft a person wrote, or its stored canonical form: keys keep
    # the whole label table, while the interest "cookies" is not a transport header.
    if contains_user_labelled_secret_member(value):
        raise ValueError("workbench input contains a labelled secret member")
    return value


class WorkbenchPersonaDraftV2(WorkbenchCohortDraft):
    """Fictional context and explicit causal assumptions; context derives no traits."""

    persona_id: PersonaId
    count: int = Field(ge=1, le=30)
    fictional: Literal[True]
    display_name: PublicText
    age: int | None = Field(default=None, ge=18, le=65)
    occupation: PublicText | None = None
    background: Annotated[str, Field(max_length=800)] | None = None
    mbti: MbtiLabel | None = None

    @model_validator(mode="before")
    @classmethod
    def no_structural_secrets(cls, value: object) -> object:
        return _reject_structural_secrets(value)

    @field_validator("fictional", mode="before")
    @classmethod
    def exact_fictional_disclosure(cls, value: object) -> object:
        if value is not True:
            raise ValueError("persona fictional must be boolean true")
        return value

    @field_validator("persona_id", "display_name", "occupation", "background", "mbti")
    @classmethod
    def safe_persona_text(cls, value: str | None) -> str | None:
        return None if value is None else _safe_persona_text(value)

    @field_validator("interests")
    @classmethod
    def safe_persona_interests(cls, value: frozenset[str]) -> frozenset[str]:
        for interest in value:
            _safe_persona_text(interest)
            # Sets have no adjacency: try each member as a label its companions follow.
            _reject_structural_secrets((interest, *(value - {interest})))
        return value

    @model_validator(mode="after")
    def bounded_persona_bytes(self) -> Self:
        _reject_structural_secrets(self.model_dump(mode="json"))
        if len(canonical_json(self).encode("utf-8")) > MAX_WORKBENCH_PERSONA_BYTES:
            raise ValueError("workbench persona exceeds its canonical size limit")
        return self


def validate_workbench_population_bytes(value: DomainModel | dict[str, object]) -> None:
    """Apply the independent population ceiling to canonical UTF-8, before persistence."""
    if len(canonical_json(value).encode("utf-8")) > MAX_WORKBENCH_POPULATION_BYTES:
        raise ValueError("workbench population exceeds its canonical size limit")


class WorkbenchPopulationDraftV2(DomainModel):
    personas: tuple[WorkbenchPersonaDraftV2, ...] = Field(min_length=1, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def bounded_raw_population(cls, value: object, info: ValidationInfo) -> object:
        _reject_structural_secrets(value)
        if info.mode == "json" and isinstance(value, dict):
            validate_workbench_population_bytes(value)
            # A model-before validator receives Python lists even in JSON mode;
            # preserve the wire array contract before strict tuple validation.
            value = dict(value)
            for field in ("personas", "assignments"):
                if isinstance(value.get(field), list):
                    value[field] = tuple(value[field])
        return value

    @model_validator(mode="after")
    def canonical_personas(self) -> Self:
        personas = tuple(sorted(self.personas, key=lambda item: item.persona_id))
        if len({item.persona_id for item in personas}) != len(personas):
            raise ValueError("workbench persona identifiers must be unique")
        if sum(item.count for item in personas) > 30:
            raise ValueError("workbench population count exceeds 30 agents")
        object.__setattr__(self, "personas", personas)
        validate_workbench_population_bytes(self)
        return self


class WorkbenchPersonaAssignmentV2(DomainModel):
    agent_id: str = Field(pattern=r"^person-(?:00[1-9]|0[12][0-9]|030)$")
    persona_id: PersonaId


def _population_assignments(
    personas: tuple[WorkbenchPersonaDraftV2, ...],
) -> tuple[WorkbenchPersonaAssignmentV2, ...]:
    assignments: list[WorkbenchPersonaAssignmentV2] = []
    for persona in personas:
        for _ in range(persona.count):
            assignments.append(
                WorkbenchPersonaAssignmentV2(
                    agent_id=f"person-{len(assignments) + 1:03d}", persona_id=persona.persona_id
                )
            )
    return tuple(assignments)


class NormalizedWorkbenchPopulationV2(WorkbenchPopulationDraftV2):
    assignments: tuple[WorkbenchPersonaAssignmentV2, ...] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def exact_canonical_assignments(self) -> Self:
        assignments = tuple(sorted(self.assignments, key=lambda item: item.agent_id))
        if assignments != _population_assignments(self.personas):
            raise ValueError("workbench assignments must exactly match canonical persona counts")
        object.__setattr__(self, "assignments", assignments)
        return self


def materialize_workbench_population(
    population: WorkbenchPopulationDraftV2, *, agent_count: int
) -> NormalizedWorkbenchPopulationV2:
    """Assign canonical contiguous agent IDs to the submitted immutable persona IDs."""
    if type(agent_count) is not int or not 1 <= agent_count <= 30:
        raise ValueError("workbench agent count must be an integer from 1 to 30")
    if sum(item.count for item in population.personas) != agent_count:
        raise ValueError("workbench persona count total must equal settings agent count")
    return NormalizedWorkbenchPopulationV2(
        personas=population.personas, assignments=_population_assignments(population.personas)
    )


class WorkbenchRunSettings(DomainModel):
    run_id: str
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    seed: int = Field(ge=0, le=2**63 - 1)
    response_mode: Literal["deterministic-rules"] = "deterministic-rules"

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        return _safe_identifier(validate_portable_run_identifier(value))


def _validate_cognition_selection(
    *,
    mode: str,
    provider_profile_id: str | None,
    provider_revision_sha256: str | None,
    aggregate_output_token_ceiling: int,
) -> None:
    offline = mode in {"rules", "mock"}
    if offline:
        if (
            provider_profile_id is not None
            or provider_revision_sha256 is not None
            or aggregate_output_token_ceiling != 0
        ):
            raise ValueError("rules and mock cognition cannot carry provider configuration")
        return
    if provider_profile_id is None or provider_revision_sha256 is None:
        raise ValueError("local and remote cognition require one exact provider revision")
    if aggregate_output_token_ceiling % 1_536 != 0:
        raise ValueError("provider cognition token ceiling must reserve whole requests")


class WorkbenchCognitionDraftV3(DomainModel):
    """One non-secret narrative mode selection submitted by the local browser."""

    model_config = ConfigDict(hide_input_in_errors=True)

    mode: Literal["rules", "mock", "local", "remote"]
    provider_profile_id: Annotated[str | None, Field(pattern=_PROVIDER_PROFILE_ID_PATTERN)]
    provider_revision_sha256: Annotated[str | None, Field(pattern=_SHA256_PATTERN)]
    aggregate_output_token_ceiling: int = Field(ge=0, le=92_160)

    @model_validator(mode="wrap")
    @classmethod
    def no_structural_secrets(
        cls,
        value: object,
        handler: ModelWrapValidatorHandler[Self],
    ) -> Self:
        _reject_structural_secrets(value)
        return handler(value)

    @field_validator("provider_profile_id")
    @classmethod
    def safe_provider_profile_id(cls, value: str | None) -> str | None:
        return _safe_provider_profile_id(value)

    @field_validator("aggregate_output_token_ceiling", mode="before")
    @classmethod
    def exact_aggregate_output_token_ceiling(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("aggregate_output_token_ceiling must be an integer")
        return value

    @model_validator(mode="after")
    def coherent_selection(self) -> Self:
        _validate_cognition_selection(
            mode=self.mode,
            provider_profile_id=self.provider_profile_id,
            provider_revision_sha256=self.provider_revision_sha256,
            aggregate_output_token_ceiling=self.aggregate_output_token_ceiling,
        )
        return self


class NormalizedWorkbenchCognitionV3(DomainModel):
    """Frozen schema-3 policy ceilings; provider profiles may only narrow them later."""

    model_config = ConfigDict(hide_input_in_errors=True)

    mode: Literal["rules", "mock", "local", "remote"]
    provider_profile_id: Annotated[str | None, Field(pattern=_PROVIDER_PROFILE_ID_PATTERN)]
    provider_revision_sha256: Annotated[str | None, Field(pattern=_SHA256_PATTERN)]
    aggregate_output_token_ceiling: int = Field(ge=0, le=92_160)
    prompt_contract_version: Literal[1]
    result_schema_version: Literal[1]
    selection_policy_version: Literal[1]
    fallback_policy_version: Literal[1]
    selected_requests_per_agent: Literal[4]
    selected_requests_per_run: Literal[60]
    concurrency_limit: Literal[4]
    max_request_bytes: Literal[8192]
    max_wire_response_bytes: Literal[1048576]
    max_output_tokens_per_attempt: Literal[384]
    reservation_tokens_per_request: Literal[1536]
    initial_attempts: Literal[1]
    transient_retries: Literal[2]
    repair_attempts: Literal[1]
    attempt_timeout_seconds: Literal[30]
    aggregate_deadline_seconds: Literal[180]

    @model_validator(mode="wrap")
    @classmethod
    def no_structural_secrets(
        cls,
        value: object,
        handler: ModelWrapValidatorHandler[Self],
    ) -> Self:
        _reject_structural_secrets(value)
        return handler(value)

    @field_validator("provider_profile_id")
    @classmethod
    def safe_provider_profile_id(cls, value: str | None) -> str | None:
        return _safe_provider_profile_id(value)

    @field_validator(
        "aggregate_output_token_ceiling",
        "prompt_contract_version",
        "result_schema_version",
        "selection_policy_version",
        "fallback_policy_version",
        "selected_requests_per_agent",
        "selected_requests_per_run",
        "concurrency_limit",
        "max_request_bytes",
        "max_wire_response_bytes",
        "max_output_tokens_per_attempt",
        "reservation_tokens_per_request",
        "initial_attempts",
        "transient_retries",
        "repair_attempts",
        "attempt_timeout_seconds",
        "aggregate_deadline_seconds",
        mode="before",
    )
    @classmethod
    def exact_integer_fields(cls, value: object, info: ValidationInfo) -> object:
        if type(value) is not int:
            raise ValueError(f"{info.field_name} must be an integer")
        return value

    @model_validator(mode="after")
    def coherent_selection(self) -> Self:
        _validate_cognition_selection(
            mode=self.mode,
            provider_profile_id=self.provider_profile_id,
            provider_revision_sha256=self.provider_revision_sha256,
            aggregate_output_token_ceiling=self.aggregate_output_token_ceiling,
        )
        return self


# Server-side defaults for a new browser draft that omits its AI limit: online AI asks
# about every noticed ad (up to the per-run cap); a local server answers one at a time.
WORKBENCH_DEFAULT_FUNDED_RESPONSE_LIMITS: Final[Mapping[str, int]] = {
    "rules": CITY_COGNITION_SELECTION_LIMITS[
        CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION
    ].offline_funded_response_limit,
    "mock": CITY_COGNITION_SELECTION_LIMITS[
        CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION
    ].offline_funded_response_limit,
    "local": 120,
    "remote": 1000,
}
_OFFLINE_FUNDED_RESPONSE_LIMITS: Final = frozenset(
    limits.offline_funded_response_limit for limits in CITY_COGNITION_SELECTION_LIMITS.values()
)


def workbench_selection_limit_refusal(
    *, mode: str, funded_response_limit: int, selection_policy_version: int
) -> str | None:
    """Why one draft limit is not allowed by a selection-policy version, if it is not."""
    limits = CITY_COGNITION_SELECTION_LIMITS[selection_policy_version]
    if mode in {"rules", "mock"}:
        if funded_response_limit != limits.offline_funded_response_limit:
            return "Offline cognition answers every selected request."
        return None
    if funded_response_limit not in limits.funded_response_limits:
        return "The AI limit is not one of the published options."
    return None


class WorkbenchCognitionDraftV4(DomainModel):
    """The browser chooses only scope, source, immutable profile, and whole requests.

    The limit is a union of every selection-policy version's options so stored schema-4
    documents keep validating; a new run is checked against the current version when it
    is constructed, and a stored one against its own frozen version.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    effect_scope: Literal["bounded-signals-v1"]
    mode: Literal["rules", "mock", "local", "remote"]
    provider_profile_id: Annotated[str | None, Field(pattern=_PROVIDER_PROFILE_ID_PATTERN)]
    provider_revision_sha256: Annotated[str | None, Field(pattern=_SHA256_PATTERN)]
    funded_response_limit: CityCognitionFundedResponseLimit
    # The languages to write each person's inner thoughts in; empty means no thoughts.
    # Omitted from canonical bytes when empty, so every draft without them keeps its hash.
    thought_languages: tuple[CityCognitionThoughtLanguageV1, ...] = Field(
        default=(), max_length=3, strict=False, exclude_if=_no_thought_languages
    )

    @model_validator(mode="wrap")
    @classmethod
    def bounded_browser_selection(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        _reject_structural_secrets(value)
        if isinstance(value, Mapping) and "funded_response_limit" not in value:
            mode = value.get("mode")
            if isinstance(mode, str) and mode in WORKBENCH_DEFAULT_FUNDED_RESPONSE_LIMITS:
                value = {
                    **value,
                    "funded_response_limit": WORKBENCH_DEFAULT_FUNDED_RESPONSE_LIMITS[mode],
                }
        return handler(value)

    @field_validator("provider_profile_id")
    @classmethod
    def safe_provider_profile_id(cls, value: str | None) -> str | None:
        return _safe_provider_profile_id(value)

    @field_validator("funded_response_limit", mode="before")
    @classmethod
    def exact_funded_limit(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("funded_response_limit must be an integer")
        return value

    @field_validator("thought_languages")
    @classmethod
    def canonical_thought_languages(
        cls, value: tuple[CityCognitionThoughtLanguageV1, ...]
    ) -> tuple[CityCognitionThoughtLanguageV1, ...]:
        if value != city_cognition_thought_languages(value):
            raise ValueError("thought languages must be distinct and in the order en, de, fa")
        return value

    @model_validator(mode="after")
    def coherent_selection(self) -> Self:
        if self.mode in {"rules", "mock"}:
            if (
                self.provider_profile_id is not None
                or self.provider_revision_sha256 is not None
                or self.funded_response_limit not in _OFFLINE_FUNDED_RESPONSE_LIMITS
            ):
                raise ValueError("offline cognition requires no profile and all selected responses")
            if self.thought_languages:
                raise ValueError("inner thoughts need an AI source, not rules or practice answers")
        elif self.provider_profile_id is None or self.provider_revision_sha256 is None:
            raise ValueError("provider cognition requires an exact profile revision")
        return self


class NormalizedWorkbenchCognitionV4(DomainModel):
    """Complete server-derived bounded-signal authority, independent of V3 policy."""

    model_config = ConfigDict(hide_input_in_errors=True)

    effect_scope: Literal["bounded-signals-v1"]
    mode: Literal["rules", "mock", "local", "remote"]
    provider_profile_id: Annotated[str | None, Field(pattern=_PROVIDER_PROFILE_ID_PATTERN)]
    provider_revision_sha256: Annotated[str | None, Field(pattern=_SHA256_PATTERN)]
    funded_response_limit: CityCognitionFundedResponseLimit
    thought_languages: tuple[CityCognitionThoughtLanguageV1, ...] = Field(
        default=(), max_length=3, strict=False, exclude_if=_no_thought_languages
    )
    causal_contract: CityCognitionCausalContractV2
    causal_contract_sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    request_schema_version: Literal[2]
    # Unions of every version's values; checked against the contract's version row.
    selected_requests_per_agent: Literal[4, 1000]
    selected_requests_per_run: Literal[60, 1000]
    concurrency_limit: Literal[4, 8]
    # Checked against the contract's prompt-contract version row (8192 for prompt v2).
    max_request_bytes: CityCognitionMaxRequestBytes
    max_terminal_record_bytes: Literal[8192]
    max_wire_response_bytes: Literal[1048576]
    max_output_tokens_per_attempt: CityCognitionOutputTokensPerAttempt
    reservation_tokens_per_request: CityCognitionReservationTokensPerRequest
    initial_attempts: Literal[1]
    transient_retries: Literal[2, 3]
    repair_attempts: Literal[1, 2]
    attempt_timeout_seconds: Literal[30]
    aggregate_deadline_seconds: Literal[180, 1800]
    aggregate_output_token_ceiling: int = Field(ge=0, le=CITY_COGNITION_MAX_AGGREGATE_OUTPUT_TOKENS)
    response_model: Literal["spatial-response-v2"]
    rule_state_noninterference: Literal[True]

    @model_validator(mode="wrap")
    @classmethod
    def no_structural_secrets(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        _reject_structural_secrets(value)
        return handler(value)

    @field_validator("provider_profile_id")
    @classmethod
    def safe_provider_profile_id(cls, value: str | None) -> str | None:
        return _safe_provider_profile_id(value)

    @field_validator(
        "funded_response_limit",
        "request_schema_version",
        "selected_requests_per_agent",
        "selected_requests_per_run",
        "concurrency_limit",
        "max_request_bytes",
        "max_terminal_record_bytes",
        "max_wire_response_bytes",
        "max_output_tokens_per_attempt",
        "reservation_tokens_per_request",
        "initial_attempts",
        "transient_retries",
        "repair_attempts",
        "attempt_timeout_seconds",
        "aggregate_deadline_seconds",
        "aggregate_output_token_ceiling",
        mode="before",
    )
    @classmethod
    def exact_integer_fields(cls, value: object, info: ValidationInfo) -> object:
        if type(value) is not int:
            raise ValueError(f"{info.field_name} must be an integer")
        return value

    @field_validator("rule_state_noninterference", mode="before")
    @classmethod
    def exact_noninterference(cls, value: object) -> object:
        if value is not True:
            raise ValueError("rule-state noninterference must be true")
        return value

    @model_validator(mode="after")
    def coherent_frozen_authority(self) -> Self:
        WorkbenchCognitionDraftV4(
            effect_scope=self.effect_scope,
            mode=self.mode,
            provider_profile_id=self.provider_profile_id,
            provider_revision_sha256=self.provider_revision_sha256,
            funded_response_limit=self.funded_response_limit,
            thought_languages=self.thought_languages,
        )
        # Reparse nested instances as well as decoded JSON: constructed instances
        # cannot smuggle a different policy past their canonical hash checks.
        contract = CityCognitionCausalContractV2.model_validate_json(
            canonical_json(self.causal_contract)
        )
        if contract.fingerprint != self.causal_contract_sha256:
            raise ValueError("causal contract hash does not match the frozen document")
        # Every frozen ceiling is the row of the STORED selection-policy version.
        version = contract.selection_policy_version
        limits = CITY_COGNITION_SELECTION_LIMITS[version]
        if workbench_selection_limit_refusal(
            mode=self.mode,
            funded_response_limit=self.funded_response_limit,
            selection_policy_version=version,
        ):
            raise ValueError("funded response limit is not allowed by the frozen contract")
        if (
            self.selected_requests_per_agent != limits.selected_requests_per_agent
            or self.selected_requests_per_run != limits.selected_requests_per_run
            or self.concurrency_limit != limits.concurrency_limit(self.mode)
            or self.reservation_tokens_per_request != limits.reservation_tokens_per_request
            or self.max_output_tokens_per_attempt != limits.output_tokens_per_attempt
            or self.transient_retries != limits.transient_retries_ceiling
            or self.repair_attempts != limits.repair_attempts_ceiling
            or self.aggregate_deadline_seconds != limits.aggregate_deadline_seconds
        ):
            raise ValueError("frozen cognition ceilings do not match the selection policy version")
        prompt = CITY_COGNITION_PROMPT_CONTRACTS[contract.prompt_contract_version]
        if self.max_request_bytes != prompt.max_request_bytes:
            raise ValueError("frozen request ceiling does not match the prompt contract version")
        if prompt.thoughts != bool(self.thought_languages):
            raise ValueError("thought languages must be chosen exactly for the thoughts contract")
        ceiling = limits.aggregate_output_token_ceiling(self.mode, self.funded_response_limit)
        if self.aggregate_output_token_ceiling != ceiling:
            raise ValueError("aggregate output ceiling does not match the funded response limit")
        return self


class WorkbenchRunDraftV4(DomainModel):
    """Fictional population and explicitly bounded synthetic cognition selection."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[4] = 4
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: WorkbenchScenarioDraft
    population: WorkbenchPopulationDraftV2
    settings: WorkbenchRunSettings
    cognition: WorkbenchCognitionDraftV4

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def exact_population_count(self) -> Self:
        if sum(item.count for item in self.population.personas) != self.settings.agent_count:
            raise ValueError("workbench persona count total must equal settings agent count")
        return self


class WorkbenchRunDraft(DomainModel):
    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: WorkbenchScenarioDraft
    cohort: WorkbenchCohortDraft
    settings: WorkbenchRunSettings

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)


class WorkbenchRunDraftV2(DomainModel):
    schema_version: Literal[2] = 2
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: WorkbenchScenarioDraft
    population: WorkbenchPopulationDraftV2
    settings: WorkbenchRunSettings

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def exact_population_count(self) -> Self:
        if sum(item.count for item in self.population.personas) != self.settings.agent_count:
            raise ValueError("workbench persona count total must equal settings agent count")
        return self


class WorkbenchRunDraftV3(DomainModel):
    """Schema-2 fictional population plus an explicit narrative-only mode selection."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[3] = 3
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: WorkbenchScenarioDraft
    population: WorkbenchPopulationDraftV2
    settings: WorkbenchRunSettings
    cognition: WorkbenchCognitionDraftV3

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def exact_population_count(self) -> Self:
        if sum(item.count for item in self.population.personas) != self.settings.agent_count:
            raise ValueError("workbench persona count total must equal settings agent count")
        return self


class _NormalizedPlacementDraft(DomainModel):
    active_windows: tuple[SpatialActiveWindow, ...] = Field(min_length=1, max_length=14)
    frequency_cap_per_agent_per_day: int = Field(ge=1, le=100)

    @field_validator("active_windows")
    @classmethod
    def canonical_windows(
        cls, value: tuple[SpatialActiveWindow, ...]
    ) -> tuple[SpatialActiveWindow, ...]:
        return _canonical_absolute_windows(value)


class NormalizedPhonePlacementDraft(_NormalizedPlacementDraft):
    placement_id: Literal["phone-placement"] = "phone-placement"
    eligible_activities: tuple[SpatialActivity, ...] = Field(min_length=1, max_length=4)
    opportunity_probability_per_minute: float = Field(ge=0, le=1)

    @field_validator("eligible_activities")
    @classmethod
    def canonical_activities(
        cls, value: tuple[SpatialActivity, ...]
    ) -> tuple[SpatialActivity, ...]:
        if len(value) != len(set(value)):
            raise ValueError("eligible phone activities must be unique")
        return tuple(sorted(value, key=_ACTIVITY_ORDER.__getitem__))


class NormalizedRoadsidePlacementDraft(_NormalizedPlacementDraft):
    placement_id: Literal["roadside-placement"] = "roadside-placement"
    road_id: str = Field(pattern=_ID_PATTERN)
    travel_direction: TravelDirection
    road_fraction: float = Field(gt=0, lt=1)
    side: BillboardSide
    orientation_degrees: float = Field(ge=0, lt=360)
    max_view_distance_meters: float = Field(gt=0, le=1_000)

    @field_validator("road_id")
    @classmethod
    def safe_road_id(cls, value: str) -> str:
        return _safe_identifier(value)


class NormalizedWorkbenchScenarioDraft(DomainModel):
    scenario_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    campaign: WorkbenchCampaignDraft
    phone: NormalizedPhonePlacementDraft | None = None
    roadside: NormalizedRoadsidePlacementDraft | None = None

    @field_validator("scenario_id")
    @classmethod
    def safe_scenario_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        return _safe_public_text(value)

    @model_validator(mode="after")
    def at_least_one_placement(self) -> Self:
        if self.phone is None and self.roadside is None:
            raise ValueError("workbench scenario requires at least one placement")
        return self


class NormalizedWorkbenchRunDraft(DomainModel):
    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: NormalizedWorkbenchScenarioDraft
    cohort: WorkbenchCohortDraft
    settings: WorkbenchRunSettings

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)


class NormalizedWorkbenchRunDraftV2(DomainModel):
    schema_version: Literal[2] = 2
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: NormalizedWorkbenchScenarioDraft
    population: NormalizedWorkbenchPopulationV2
    settings: WorkbenchRunSettings

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def exact_population_count(self) -> Self:
        if len(self.population.assignments) != self.settings.agent_count:
            raise ValueError("workbench assignment count must equal settings agent count")
        return self


class NormalizedWorkbenchRunDraftV3(DomainModel):
    """Canonical schema-3 scientific draft and frozen narrative policy ceilings."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[3] = 3
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: NormalizedWorkbenchScenarioDraft
    population: NormalizedWorkbenchPopulationV2
    settings: WorkbenchRunSettings
    cognition: NormalizedWorkbenchCognitionV3

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def exact_population_count(self) -> Self:
        if len(self.population.assignments) != self.settings.agent_count:
            raise ValueError("workbench assignment count must equal settings agent count")
        return self


class NormalizedWorkbenchRunDraftV4(DomainModel):
    """Canonical scientific draft with the complete frozen V2 causal contract."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[4] = 4
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: NormalizedWorkbenchScenarioDraft
    population: NormalizedWorkbenchPopulationV2
    settings: WorkbenchRunSettings
    cognition: NormalizedWorkbenchCognitionV4

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def exact_population_count(self) -> Self:
        if len(self.population.assignments) != self.settings.agent_count:
            raise ValueError("workbench assignment count must equal settings agent count")
        return self


class CreativeTemplate(DomainModel):
    template_id: str = Field(pattern=_ID_PATTERN)
    template_version: Literal[1] = 1
    product_name: str = Field(min_length=1, max_length=80)
    product_category: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=240)
    call_to_action: str = Field(min_length=1, max_length=120)
    disclosure: Literal["Fictional creative for synthetic simulation only."] = CREATIVE_DISCLOSURE

    @field_validator("template_id")
    @classmethod
    def safe_template_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("template_version", mode="before")
    @classmethod
    def exact_template_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("creative template_version must be integer 1")
        return value

    @field_validator(
        "product_name",
        "product_category",
        "message",
        "call_to_action",
        "disclosure",
    )
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_public_text(value)

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


class CreativeTemplateCatalog(DomainModel):
    schema_version: Literal[1] = 1
    templates: tuple[CreativeTemplate, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_sorted_templates(self) -> Self:
        templates = tuple(sorted(self.templates, key=lambda item: item.template_id))
        identifiers = tuple(item.template_id for item in templates)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate creative template identifier")
        object.__setattr__(self, "templates", templates)
        return self


class WorkbenchRunInput(DomainModel):
    schema_version: Literal[1] = 1
    draft: NormalizedWorkbenchRunDraft
    creative_template: CreativeTemplate

    @model_validator(mode="after")
    def creative_matches_draft(self) -> Self:
        if self.creative_template.template_id != self.draft.scenario.campaign.creative_template_id:
            raise ValueError("creative template does not match workbench campaign")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


class WorkbenchRunInputV2(DomainModel):
    schema_version: Literal[2] = 2
    draft: NormalizedWorkbenchRunDraftV2
    creative_template: CreativeTemplate

    @model_validator(mode="after")
    def creative_matches_draft(self) -> Self:
        if self.creative_template.template_id != self.draft.scenario.campaign.creative_template_id:
            raise ValueError("creative template does not match workbench campaign")
        if len(canonical_json(self).encode("utf-8")) > MAX_WORKBENCH_INPUT_BYTES:
            raise ValueError("workbench input exceeds its canonical size limit")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


class WorkbenchRunInputV3(DomainModel):
    """Frozen schema-3 input; cognition configuration has no scientific authority."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[3] = 3
    draft: NormalizedWorkbenchRunDraftV3
    creative_template: CreativeTemplate

    @model_validator(mode="after")
    def creative_matches_draft(self) -> Self:
        if self.creative_template.template_id != self.draft.scenario.campaign.creative_template_id:
            raise ValueError("creative template does not match workbench campaign")
        if len(canonical_json(self).encode("utf-8")) > MAX_WORKBENCH_INPUT_BYTES:
            raise ValueError("workbench input exceeds its canonical size limit")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


class WorkbenchRunInputV4(DomainModel):
    """Frozen schema-4 input; providers affect only the separate cognition track."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[4] = 4
    draft: NormalizedWorkbenchRunDraftV4
    creative_template: CreativeTemplate

    @model_validator(mode="after")
    def creative_matches_draft(self) -> Self:
        if self.creative_template.template_id != self.draft.scenario.campaign.creative_template_id:
            raise ValueError("creative template does not match workbench campaign")
        if len(canonical_json(self).encode("utf-8")) > MAX_WORKBENCH_INPUT_BYTES:
            raise ValueError("workbench input exceeds its canonical size limit")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


WorkbenchRunDraftDocument: TypeAlias = (
    WorkbenchRunDraft | WorkbenchRunDraftV2 | WorkbenchRunDraftV3 | WorkbenchRunDraftV4
)
WorkbenchRunInputDocument: TypeAlias = (
    WorkbenchRunInput | WorkbenchRunInputV2 | WorkbenchRunInputV3 | WorkbenchRunInputV4
)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("workbench input JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"workbench input JSON contains non-finite constant {value}")


def parse_workbench_run_input_json(document: str | bytes) -> WorkbenchRunInputDocument:
    """Parse one strict persisted input without accepting ambiguous JSON."""
    try:
        encoded = document.encode("utf-8") if isinstance(document, str) else document
        if len(encoded) > MAX_WORKBENCH_INPUT_BYTES:
            raise ValueError("workbench input exceeds its size limit")
        payload = json.loads(
            encoded.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("workbench input is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("workbench input must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int or version not in (1, 2, 3, 4):
        raise ValueError("unsupported workbench input schema version")
    if version in (2, 3, 4):
        _reject_structural_secrets(payload)
    normalized = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if version == 1:
        return WorkbenchRunInput.model_validate_json(normalized)
    if version == 2:
        return WorkbenchRunInputV2.model_validate_json(normalized)
    if version == 3:
        return WorkbenchRunInputV3.model_validate_json(normalized)
    return WorkbenchRunInputV4.model_validate_json(normalized)


__all__ = [
    "CREATIVE_DISCLOSURE",
    "CreativeTemplate",
    "CreativeTemplateCatalog",
    "NormalizedPhonePlacementDraft",
    "NormalizedRoadsidePlacementDraft",
    "NormalizedWorkbenchCognitionV3",
    "NormalizedWorkbenchCognitionV4",
    "NormalizedWorkbenchPopulationV2",
    "NormalizedWorkbenchRunDraft",
    "NormalizedWorkbenchRunDraftV2",
    "NormalizedWorkbenchRunDraftV3",
    "NormalizedWorkbenchRunDraftV4",
    "NormalizedWorkbenchScenarioDraft",
    "WorkbenchCampaignDraft",
    "WorkbenchCognitionDraftV3",
    "WorkbenchCognitionDraftV4",
    "WorkbenchCohortDraft",
    "WorkbenchDailyWindow",
    "WorkbenchInitialState",
    "WorkbenchPersonaAssignmentV2",
    "WorkbenchPersonaDraftV2",
    "WorkbenchPhonePlacementDraft",
    "WorkbenchPopulationDraftV2",
    "WorkbenchRoadsidePlacementDraft",
    "WorkbenchRunDraft",
    "WorkbenchRunDraftDocument",
    "WorkbenchRunDraftV2",
    "WorkbenchRunDraftV3",
    "WorkbenchRunDraftV4",
    "WorkbenchRunInput",
    "WorkbenchRunInputDocument",
    "WorkbenchRunInputV2",
    "WorkbenchRunInputV3",
    "WorkbenchRunInputV4",
    "WorkbenchRunSettings",
    "WorkbenchScenarioDraft",
    "materialize_workbench_population",
    "parse_workbench_run_input_json",
    "validate_workbench_population_bytes",
]
