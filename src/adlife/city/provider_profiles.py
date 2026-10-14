"""Strict immutable provider-profile revisions for the local city workbench."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Literal, Self
from unicodedata import bidirectional

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from adlife.city.provider_runtime import normalize_provider_origin
from adlife.city.workbench_immutable_registry import (
    ImmutableWorkspacePublicationError,
    ImmutableWorkspaceRecord,
    ImmutableWorkspaceRegistry,
    ImmutableWorkspaceRegistryConflict,
    ImmutableWorkspaceRegistryLayout,
    ImmutableWorkspaceRegistryUnavailable,
)
from adlife.city.workbench_security import WorkbenchJsonError, strict_json_object
from adlife.city.workbench_workspace import WorkbenchWorkspace
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_OUTPUT_TOKENS_PER_ATTEMPT,
    MAX_CITY_COGNITION_ATTEMPT_TIMEOUT_SECONDS,
    MAX_CITY_COGNITION_REPAIR_ATTEMPTS,
    MAX_CITY_COGNITION_TRANSIENT_RETRIES,
    Hash,
    canonical_city_cognition_provider_profile_payload,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.domain.identifiers import WINDOWS_RESERVED_RUN_IDS
from adlife.core.domain.person import (
    SECRET_LABEL_WORDS,
    DomainModel,
    contains_provider_secret_text,
)
from adlife.core.domain.serialization import canonical_json

MAX_PROVIDER_PROFILE_IDS = 20
MAX_PROVIDER_PROFILE_REVISIONS_PER_ID = 10
MAX_PROVIDER_PROFILE_REVISION_BYTES = 16_384

_PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
_RECORD_PATTERN = re.compile(r"^(?P<record_id>[0-9a-f]{64})\.json$")
_GROUP_PATTERN = re.compile(_PROFILE_ID_PATTERN)
_USER_SECRET_LABEL_PATTERN = re.compile(
    r"[A-Za-z0-9_-]{0,40}?(?:" + "|".join(SECRET_LABEL_WORDS) + r")s?",
    re.IGNORECASE,
)
_BIDI_CONTROLS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"})
_REGISTRY_LAYOUT = ImmutableWorkspaceRegistryLayout(
    records_directory="provider-profiles",
    staging_directory="provider-profile-staging",
    lock_filename=".provider-profile-registry.lock",
    group_pattern=_GROUP_PATTERN,
    record_pattern=_RECORD_PATTERN,
    max_groups=MAX_PROVIDER_PROFILE_IDS,
    max_records_per_group=MAX_PROVIDER_PROFILE_REVISIONS_PER_ID,
    max_record_bytes=MAX_PROVIDER_PROFILE_REVISION_BYTES,
)


class InvalidProviderProfile(ValueError):
    """A profile draft, lookup identity, or immutable revision is invalid."""

    def __init__(self) -> None:
        super().__init__("provider profile failed validation")


class ProviderProfileConflict(ValueError):
    """A destination already exists or the bounded registry is full."""

    def __init__(self) -> None:
        super().__init__("provider profile conflicts with workspace state")


class ProviderProfileNotFound(LookupError):
    """No exact immutable profile revision has the requested identity."""

    def __init__(self) -> None:
        super().__init__("provider profile revision was not found")


class ProviderProfileRegistryUnavailable(RuntimeError):
    """The complete immutable registry snapshot cannot be trusted."""

    def __init__(self) -> None:
        super().__init__("provider profile registry is unavailable")


class ProviderProfilePublicationError(RuntimeError):
    """Profile publication failed, with a durable-commit ambiguity marker."""

    def __init__(self, potentially_committed: bool) -> None:
        if type(potentially_committed) is not bool:
            raise TypeError("publication state must be a boolean")
        self.potentially_committed = potentially_committed
        message = (
            "provider profile may already be committed"
            if potentially_committed
            else "provider profile publication failed before commit"
        )
        super().__init__(message)


def _safe_profile_text(value: str) -> str:
    if value != value.strip() or not value:
        raise ValueError("provider profile text is blank or not normalized")
    if any(
        ord(character) < 32
        or 127 <= ord(character) < 160
        or 0xD800 <= ord(character) <= 0xDFFF
        or character in {"\u2028", "\u2029"}
        or bidirectional(character) in _BIDI_CONTROLS
        for character in value
    ):
        raise ValueError("provider profile text contains a display control character")
    if contains_provider_secret_text(value):
        raise ValueError("provider profile text contains screened credential data")
    return value


def _portable_profile_id(value: str) -> str:
    if value in WINDOWS_RESERVED_RUN_IDS or contains_provider_secret_text(value):
        raise ValueError("provider profile identifier is reserved")
    return value


def _reject_cross_field_secret_labels(values: tuple[str, ...]) -> None:
    if any(
        _USER_SECRET_LABEL_PATTERN.fullmatch(value.strip()) is not None
        and any(other.strip() for other in values[:index] + values[index + 1 :])
        for index, value in enumerate(values)
    ):
        raise ValueError("provider profile contains labelled credential data")


class _ProviderProfileModel(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True, revalidate_instances="always")


class ProviderProfileDraftV1(_ProviderProfileModel):
    """One bounded non-secret profile draft, before server-owned revision binding."""

    schema_version: Literal[1] = 1
    profile_id: str = Field(pattern=_PROFILE_ID_PATTERN)
    display_label: str = Field(min_length=1, max_length=80)
    mode: Literal["local", "remote"]
    base_url: str = Field(min_length=1, max_length=2_048)
    model_id: str = Field(min_length=1, max_length=120)
    protocol: Literal["openai-chat-completions-json-schema-v1"]
    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)
    seed_policy: Literal["omit", "fixed"]
    seed: int | None = Field(ge=0, le=2**63 - 1)
    max_output_tokens: int = Field(ge=1, le=CITY_COGNITION_OUTPUT_TOKENS_PER_ATTEMPT)
    attempt_timeout_seconds: int = Field(ge=1, le=MAX_CITY_COGNITION_ATTEMPT_TIMEOUT_SECONDS)
    transient_retries: int = Field(ge=0, le=MAX_CITY_COGNITION_TRANSIENT_RETRIES)
    repair_attempts: int = Field(ge=0, le=MAX_CITY_COGNITION_REPAIR_ATTEMPTS)
    origin_approval_id: str = Field(pattern=_PROFILE_ID_PATTERN)

    @field_validator("profile_id", "origin_approval_id")
    @classmethod
    def portable_identifier(cls, value: str) -> str:
        return _portable_profile_id(value)

    @field_validator("display_label", "base_url", "model_id")
    @classmethod
    def bounded_nonsecret_text(cls, value: str) -> str:
        return _safe_profile_text(value)

    @model_validator(mode="after")
    def coherent_profile(self) -> Self:
        if (self.seed_policy == "fixed") != (self.seed is not None):
            raise ValueError("provider seed must be present exactly for fixed seed policy")
        normalized_base_url, trust_class = normalize_provider_origin(self.base_url)
        if self.base_url != normalized_base_url or self.mode != trust_class:
            raise ValueError("provider profile mode does not match its base URL")
        _reject_cross_field_secret_labels(
            (
                self.profile_id,
                self.display_label,
                self.mode,
                self.base_url,
                self.model_id,
                self.protocol,
                self.seed_policy,
                self.origin_approval_id,
            )
        )
        return self


def _draft_facts(value: ProviderProfileDraftV1 | ProviderProfileRevisionV1) -> dict[str, object]:
    return value.model_dump(mode="json", exclude={"revision_sha256", "credential_slot_id"})


def _credential_slot_id(value: ProviderProfileDraftV1) -> str:
    return sha256(canonical_city_cognition_provider_profile_payload(value)).hexdigest()


class ProviderProfileRevisionV1(ProviderProfileDraftV1):
    """One exact immutable profile revision with a server-derived credential slot."""

    revision_sha256: Hash
    credential_slot_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def bound_server_metadata(self) -> Self:
        draft = ProviderProfileDraftV1.model_validate(_draft_facts(self))
        if self.credential_slot_id != _credential_slot_id(draft):
            raise ValueError("provider credential slot does not match the profile facts")
        if self.revision_sha256 != city_cognition_provider_profile_revision_sha256(self):
            raise ValueError("provider revision hash does not match the canonical facts payload")
        return self


def _build_revision(draft: ProviderProfileDraftV1) -> ProviderProfileRevisionV1:
    credential_slot_id = _credential_slot_id(draft)
    facts = {**draft.model_dump(mode="json"), "credential_slot_id": credential_slot_id}
    revision_sha256 = city_cognition_provider_profile_revision_sha256(facts)
    return ProviderProfileRevisionV1.model_validate({**facts, "revision_sha256": revision_sha256})


def _canonical_revision_bytes(revision: ProviderProfileRevisionV1) -> bytes:
    encoded = (canonical_json(revision) + "\n").encode("utf-8")
    if len(encoded) > MAX_PROVIDER_PROFILE_REVISION_BYTES:
        raise InvalidProviderProfile()
    return encoded


def _parse_record(record: ImmutableWorkspaceRecord) -> ProviderProfileRevisionV1:
    try:
        payload = strict_json_object(
            record.document,
            max_body_bytes=MAX_PROVIDER_PROFILE_REVISION_BYTES,
        )
        revision = ProviderProfileRevisionV1.model_validate(payload)
        canonical = _canonical_revision_bytes(revision)
    except (InvalidProviderProfile, ValidationError, WorkbenchJsonError, ValueError):
        raise ProviderProfileRegistryUnavailable() from None
    if (
        revision.profile_id != record.group_id
        or revision.revision_sha256 != record.record_id
        or canonical != record.document
    ):
        raise ProviderProfileRegistryUnavailable()
    return revision


def _parse_snapshot(
    records: tuple[ImmutableWorkspaceRecord, ...],
) -> tuple[ProviderProfileRevisionV1, ...]:
    revisions = tuple(_parse_record(record) for record in records)
    identities = {(item.profile_id, item.revision_sha256) for item in revisions}
    if len(identities) != len(revisions):
        raise ProviderProfileRegistryUnavailable()
    return tuple(sorted(revisions, key=lambda item: (item.profile_id, item.revision_sha256)))


def _validate_lookup_identity(profile_id: object, revision_sha256: object) -> tuple[str, str]:
    if (
        not isinstance(profile_id, str)
        or _GROUP_PATTERN.fullmatch(profile_id) is None
        or profile_id in WINDOWS_RESERVED_RUN_IDS
        or contains_provider_secret_text(profile_id)
        or not isinstance(revision_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", revision_sha256) is None
    ):
        raise InvalidProviderProfile()
    return profile_id, revision_sha256


class ProviderProfileRegistry:
    """List, load, and exclusively create immutable provider-profile revisions."""

    def __init__(self, workspace: WorkbenchWorkspace) -> None:
        if not isinstance(workspace, WorkbenchWorkspace):
            raise TypeError("workspace must be a WorkbenchWorkspace")
        self.workspace = workspace
        self._store = ImmutableWorkspaceRegistry(workspace, _REGISTRY_LAYOUT)

    def list(self) -> tuple[ProviderProfileRevisionV1, ...]:
        """Return the complete validated snapshot in stable identity order."""
        try:
            with self._store.locked() as transaction:
                return _parse_snapshot(transaction.discover())
        except ProviderProfileRegistryUnavailable:
            raise
        except ImmutableWorkspaceRegistryUnavailable:
            raise ProviderProfileRegistryUnavailable() from None

    def load(self, profile_id: str, revision_sha256: str) -> ProviderProfileRevisionV1:
        """Load one exact revision only after validating the registry as a whole."""
        checked_profile_id, checked_revision = _validate_lookup_identity(
            profile_id, revision_sha256
        )
        for revision in self.list():
            if (
                revision.profile_id == checked_profile_id
                and revision.revision_sha256 == checked_revision
            ):
                return revision
        raise ProviderProfileNotFound()

    def create(self, draft: ProviderProfileDraftV1) -> ProviderProfileRevisionV1:
        """Revalidate and no-clobber one canonical server-bound profile revision."""
        if not isinstance(draft, ProviderProfileDraftV1):
            raise TypeError("provider profile draft must be a ProviderProfileDraftV1")
        try:
            checked = ProviderProfileDraftV1.model_validate(
                draft.model_dump(mode="json", warnings="none")
            )
            revision = _build_revision(checked)
            canonical = _canonical_revision_bytes(revision)
        except (InvalidProviderProfile, ValidationError, ValueError):
            raise InvalidProviderProfile() from None

        publication_completed = False
        try:
            with self._store.locked() as transaction:
                before_records = transaction.discover()
                before = _parse_snapshot(before_records)
                profile_ids = {item.profile_id for item in before}
                profile_revisions = tuple(
                    item for item in before if item.profile_id == revision.profile_id
                )
                if any(item.revision_sha256 == revision.revision_sha256 for item in before):
                    raise ProviderProfileConflict()
                if (
                    revision.profile_id not in profile_ids
                    and len(profile_ids) >= MAX_PROVIDER_PROFILE_IDS
                ):
                    raise ProviderProfileConflict()
                if len(profile_revisions) >= MAX_PROVIDER_PROFILE_REVISIONS_PER_ID:
                    raise ProviderProfileConflict()
                transaction.publish(
                    revision.profile_id,
                    revision.revision_sha256,
                    canonical,
                )
                publication_completed = True
                after_records = transaction.discover()
                after = _parse_snapshot(after_records)
                expected = tuple(
                    sorted(
                        (*before, revision),
                        key=lambda item: (item.profile_id, item.revision_sha256),
                    )
                )
                if after != expected:
                    raise ProviderProfilePublicationError(True)
                return revision
        except ProviderProfileConflict:
            raise
        except ProviderProfilePublicationError:
            raise
        except ImmutableWorkspaceRegistryConflict:
            raise ProviderProfileConflict() from None
        except ImmutableWorkspacePublicationError as error:
            raise ProviderProfilePublicationError(error.potentially_committed) from None
        except (ImmutableWorkspaceRegistryUnavailable, ProviderProfileRegistryUnavailable):
            if publication_completed:
                raise ProviderProfilePublicationError(True) from None
            raise ProviderProfileRegistryUnavailable() from None


__all__ = [
    "MAX_PROVIDER_PROFILE_IDS",
    "MAX_PROVIDER_PROFILE_REVISIONS_PER_ID",
    "MAX_PROVIDER_PROFILE_REVISION_BYTES",
    "InvalidProviderProfile",
    "ProviderProfileConflict",
    "ProviderProfileDraftV1",
    "ProviderProfileNotFound",
    "ProviderProfilePublicationError",
    "ProviderProfileRegistry",
    "ProviderProfileRegistryUnavailable",
    "ProviderProfileRevisionV1",
]
