from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import (
    SpatialStudyDefinition,
    SpatialStudyPair,
    parse_spatial_study_definition_json,
)


def _pair(
    seed: int,
    *,
    control_run_id: str | None = None,
    treatment_run_id: str | None = None,
) -> SpatialStudyPair:
    return SpatialStudyPair(
        seed=seed,
        control_run_id=control_run_id or f"control-{seed}",
        treatment_run_id=treatment_run_id or f"treatment-{seed}",
    )


def _definition(
    *,
    study_id: str = "phone-vs-roadside",
    design: str = "paired-contrast",
    analysis_scope: str = "attention-and-response",
    pairs: tuple[SpatialStudyPair, ...] | None = None,
) -> SpatialStudyDefinition:
    return SpatialStudyDefinition(
        study_id=study_id,
        design=design,
        analysis_scope=analysis_scope,
        pairs=pairs if pairs is not None else (_pair(0), _pair(1)),
    )


def _definition_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "study_id": "phone-vs-roadside",
        "design": "paired-contrast",
        "analysis_scope": "attention-and-response",
        "pairs": [
            {
                "seed": 0,
                "control_run_id": "control-0",
                "treatment_run_id": "treatment-0",
            },
            {
                "seed": 1,
                "control_run_id": "control-1",
                "treatment_run_id": "treatment-1",
            },
        ],
    }


def test_spatial_study_definition_has_exact_minimal_schema_and_fingerprint() -> None:
    result = _definition()
    expected = _definition_document()
    canonical = canonical_json(expected)

    assert set(SpatialStudyPair.model_fields) == {
        "seed",
        "control_run_id",
        "treatment_run_id",
    }
    assert set(SpatialStudyDefinition.model_fields) == {
        "schema_version",
        "study_id",
        "design",
        "analysis_scope",
        "pairs",
    }
    assert result.model_dump(mode="json") == expected
    assert canonical == (
        '{"analysis_scope":"attention-and-response","design":"paired-contrast",'
        '"pairs":[{"control_run_id":"control-0","seed":0,'
        '"treatment_run_id":"treatment-0"},{"control_run_id":"control-1",'
        '"seed":1,"treatment_run_id":"treatment-1"}],"schema_version":1,'
        '"study_id":"phone-vs-roadside"}'
    )
    assert result.fingerprint == sha256(canonical.encode("utf-8")).hexdigest()
    assert result.fingerprint == (
        "e378e152241f1a5cb6db1d3460a47505dfaf6d872acc26b3b58409beba3eab97"
    )
    assert parse_spatial_study_definition_json(canonical) == result
    assert parse_spatial_study_definition_json(canonical.encode("utf-8")) == result

    with pytest.raises(ValidationError, match="frozen"):
        result.__setattr__("study_id", "other-study")
    with pytest.raises(ValidationError, match="frozen"):
        result.pairs[0].__setattr__("seed", 9)


@pytest.mark.parametrize("design", ["a-a", "paired-contrast"])
@pytest.mark.parametrize("analysis_scope", ["attention", "attention-and-response"])
def test_exact_public_design_and_analysis_scope_enums(
    design: str,
    analysis_scope: str,
) -> None:
    if design == "a-a":
        pairs = (
            _pair(0, control_run_id="same-0", treatment_run_id="same-0"),
            _pair(1, control_run_id="same-1", treatment_run_id="same-1"),
        )
    else:
        pairs = (_pair(0), _pair(1))

    result = _definition(
        design=design,
        analysis_scope=analysis_scope,
        pairs=pairs,
    )

    assert result.design == design
    assert result.analysis_scope == analysis_scope


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("schema_version", 0),
        ("schema_version", 2),
        ("design", "aa"),
        ("design", "A-A"),
        ("design", "contrast"),
        ("design", True),
        ("analysis_scope", "response"),
        ("analysis_scope", "both"),
        ("analysis_scope", "Attention"),
        ("analysis_scope", True),
    ],
)
def test_definition_refuses_non_exact_versions_and_enums(field: str, value: object) -> None:
    document = _definition().model_dump(mode="python")
    document[field] = value

    with pytest.raises(ValidationError):
        SpatialStudyDefinition.model_validate(document)


@pytest.mark.parametrize("seed", [True, False, 0.0, "0", -1])
def test_pair_seed_is_a_strict_nonnegative_integer(seed: object) -> None:
    with pytest.raises(ValidationError):
        SpatialStudyPair(
            seed=seed,
            control_run_id="control",
            treatment_run_id="treatment",
        )


def test_pairs_are_canonicalized_by_seed_before_hashing_or_serialization() -> None:
    expected = _definition()

    reversed_input = _definition(pairs=(_pair(1), _pair(0)))
    parsed_reversed = parse_spatial_study_definition_json(
        json.dumps(
            {
                **_definition_document(),
                "pairs": list(reversed(_definition_document()["pairs"])),
            }
        )
    )

    assert reversed_input == expected
    assert parsed_reversed == expected
    assert tuple(pair.seed for pair in reversed_input.pairs) == (0, 1)
    assert reversed_input.fingerprint == expected.fingerprint


@pytest.mark.parametrize(
    "seeds",
    [
        (),
        (0,),
        (0, 0),
        (0, 2),
        (1, 2),
        (0, 1, 3),
    ],
)
def test_definition_requires_two_or_more_exact_contiguous_seeds_from_zero(
    seeds: tuple[int, ...],
) -> None:
    with pytest.raises(ValidationError, match=r"pair|seed|contiguous"):
        _definition(pairs=tuple(_pair(seed) for seed in seeds))


def test_definition_accepts_exact_minimum_and_maximum_pair_counts() -> None:
    minimum = _definition(pairs=(_pair(0), _pair(1)))
    maximum = _definition(pairs=tuple(_pair(seed) for seed in range(100)))

    assert len(minimum.pairs) == 2
    assert len(maximum.pairs) == 100
    assert tuple(pair.seed for pair in maximum.pairs) == tuple(range(100))


def test_definition_refuses_more_than_one_hundred_pairs() -> None:
    with pytest.raises(ValidationError, match=r"100|pair"):
        _definition(pairs=tuple(_pair(seed) for seed in range(101)))


@pytest.mark.parametrize(
    "pairs",
    [
        (
            _pair(0, control_run_id="reused", treatment_run_id="treatment-0"),
            _pair(1, control_run_id="reused", treatment_run_id="treatment-1"),
        ),
        (
            _pair(0, control_run_id="control-0", treatment_run_id="reused"),
            _pair(1, control_run_id="control-1", treatment_run_id="reused"),
        ),
        (
            _pair(0, control_run_id="reused", treatment_run_id="treatment-0"),
            _pair(1, control_run_id="control-1", treatment_run_id="reused"),
        ),
    ],
)
def test_run_identifier_cannot_be_reused_under_different_seed_entries(
    pairs: tuple[SpatialStudyPair, SpatialStudyPair],
) -> None:
    with pytest.raises(ValidationError, match=r"run|reuse|seed"):
        _definition(pairs=pairs)


def test_aa_cannot_relabel_one_run_as_two_different_seed_units() -> None:
    pairs = (
        _pair(0, control_run_id="same-run", treatment_run_id="same-run"),
        _pair(1, control_run_id="same-run", treatment_run_id="same-run"),
    )

    with pytest.raises(ValidationError, match=r"run|reuse|seed"):
        _definition(design="a-a", pairs=pairs)


def test_equal_arm_run_ids_are_allowed_only_for_aa() -> None:
    same_run_pairs = (
        _pair(0, control_run_id="same-0", treatment_run_id="same-0"),
        _pair(1, control_run_id="same-1", treatment_run_id="same-1"),
    )

    aa = _definition(design="a-a", pairs=same_run_pairs)

    assert aa.pairs == same_run_pairs
    with pytest.raises(ValidationError, match=r"paired|contrast|different|same"):
        _definition(design="paired-contrast", pairs=same_run_pairs)


def test_aa_definition_may_name_distinct_runs_for_later_artifact_identity_proof() -> None:
    # Run IDs are receipts, not the scientific identity fields. The analyzer will later
    # require exact A/A simulation inputs and outputs even when the two runs have different
    # names. The definition cannot infer scenario identity from an identifier string.
    result = _definition(design="a-a", pairs=(_pair(0), _pair(1)))

    assert all(pair.control_run_id != pair.treatment_run_id for pair in result.pairs)


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "Uppercase",
        "-leading",
        "has space",
        "has/slash",
        r"has\backslash",
        "has.dot",
        "has:colon",
        "../escape",
        "a" * 41,
        "شناسه",
    ],
)
@pytest.mark.parametrize("field", ["study_id", "control_run_id", "treatment_run_id"])
def test_every_persisted_identifier_uses_the_portable_run_id_grammar(
    field: str,
    identifier: str,
) -> None:
    if field == "study_id":
        with pytest.raises(ValidationError):
            _definition(study_id=identifier)
        return
    pair_document = _pair(0).model_dump(mode="python")
    pair_document[field] = identifier
    with pytest.raises(ValidationError):
        SpatialStudyPair.model_validate(pair_document)


@pytest.mark.parametrize("identifier", ["a", "run-9", "a" * 40, "trailing-"])
def test_portable_run_id_boundary_values_are_accepted(identifier: str) -> None:
    pair = SpatialStudyPair(
        seed=0,
        control_run_id=identifier,
        treatment_run_id="treatment",
    )
    definition = _definition(study_id=identifier)

    assert pair.control_run_id == identifier
    assert definition.study_id == identifier


@pytest.mark.parametrize(
    "identifier",
    [
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com9",
        "lpt1",
        "lpt9",
    ],
)
@pytest.mark.parametrize("field", ["study_id", "control_run_id", "treatment_run_id"])
def test_windows_reserved_names_are_refused_on_every_platform(
    field: str,
    identifier: str,
) -> None:
    if field == "study_id":
        with pytest.raises(ValidationError, match=r"reserved|identifier"):
            _definition(study_id=identifier)
        return
    document = _pair(0).model_dump(mode="python")
    document[field] = identifier
    with pytest.raises(ValidationError, match=r"reserved|identifier"):
        SpatialStudyPair.model_validate(document)


@pytest.mark.parametrize(
    "identifier",
    [
        "sk-live-abcdefghij",
        "xoxb-1234567890-abcdefghij",
        "person@example.com",
        "api_key=topsecret123",
    ],
)
@pytest.mark.parametrize("field", ["study_id", "control_run_id", "treatment_run_id"])
def test_credential_shaped_identifiers_are_refused_without_echo(
    field: str,
    identifier: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        if field == "study_id":
            _definition(study_id=identifier)
        else:
            document = _pair(0).model_dump(mode="python")
            document[field] = identifier
            SpatialStudyPair.model_validate(document)

    assert identifier not in str(error.value)
    assert "identifier" in str(error.value).lower()


@pytest.mark.parametrize(
    "extra",
    [
        {"title": "Study title"},
        {"note": "Free prose"},
        {"url": "https://example.invalid"},
        {"path": "../runs"},
        {"output": "report.html"},
        {"metadata": {"owner": "someone"}},
    ],
)
def test_definition_has_no_free_prose_url_path_or_metadata_fields(
    extra: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="extra"):
        SpatialStudyDefinition.model_validate(_definition().model_dump() | extra)


def test_pair_has_no_extra_or_nested_user_controlled_fields() -> None:
    for extra in (
        {"schema_version": 1},
        {"label": "control"},
        {"path": "../run"},
        {"scenario_sha256": "a" * 64},
    ):
        with pytest.raises(ValidationError, match="extra"):
            SpatialStudyPair.model_validate(_pair(0).model_dump() | extra)


def test_semantic_definition_changes_always_change_the_fingerprint() -> None:
    baseline = _definition()
    changed_documents = (
        baseline.model_copy(update={"study_id": "different-study"}),
        _definition(design="a-a"),
        _definition(analysis_scope="attention"),
        _definition(
            pairs=(
                _pair(0, treatment_run_id="different-treatment-0"),
                _pair(1),
            )
        ),
    )

    assert all(changed.fingerprint != baseline.fingerprint for changed in changed_documents)


def test_model_copy_revalidates_nested_protocol_invariants() -> None:
    baseline = _definition()

    with pytest.raises(ValidationError):
        baseline.model_copy(update={"pairs": (_pair(0),)})
    with pytest.raises(ValidationError):
        baseline.model_copy(update={"pairs": (_pair(0), _pair(2))})
    with pytest.raises(ValidationError):
        baseline.model_copy(update={"design": "unsupported"})


def test_python_construction_does_not_coerce_a_mutable_pair_list() -> None:
    document = _definition().model_dump(mode="python")
    document["pairs"] = list(document["pairs"])

    with pytest.raises(ValidationError):
        SpatialStudyDefinition.model_validate(document)


def test_definition_revalidates_bypass_constructed_pairs() -> None:
    invalid_pair = SpatialStudyPair.model_construct(
        seed=True,
        control_run_id="control-0",
        treatment_run_id="treatment-0",
    )

    with pytest.raises(ValidationError):
        SpatialStudyDefinition(
            study_id="study",
            design="paired-contrast",
            analysis_scope="attention",
            pairs=(invalid_pair, _pair(1)),
        )


def test_parser_canonicalizes_mapping_order_and_rejects_unknown_fields() -> None:
    document = _definition_document()
    deliberately_reordered = {
        "pairs": document["pairs"],
        "analysis_scope": document["analysis_scope"],
        "study_id": document["study_id"],
        "schema_version": document["schema_version"],
        "design": document["design"],
    }

    parsed = parse_spatial_study_definition_json(
        json.dumps(deliberately_reordered, ensure_ascii=False)
    )

    assert parsed == _definition()
    with pytest.raises((ValidationError, ValueError), match="extra"):
        parse_spatial_study_definition_json(
            json.dumps(deliberately_reordered | {"title": "not supported"})
        )


@pytest.mark.parametrize(
    "document",
    [
        "[]",
        "null",
        '"study"',
        "1",
        "true",
        '{"schema_version":true}',
        '{"schema_version":1.0}',
        '{"schema_version":"1"}',
        '{"schema_version":0}',
        '{"schema_version":2}',
    ],
)
def test_parser_refuses_non_object_and_non_exact_version_documents(document: str) -> None:
    with pytest.raises((ValidationError, ValueError)):
        parse_spatial_study_definition_json(document)


@pytest.mark.parametrize(
    "document",
    [
        '{"schema_version":1,"schema_version":1}',
        (
            '{"schema_version":1,"study_id":"study","design":"paired-contrast",'
            '"analysis_scope":"attention","pairs":['
            '{"seed":0,"seed":0,"control_run_id":"c0","treatment_run_id":"t0"},'
            '{"seed":1,"control_run_id":"c1","treatment_run_id":"t1"}]}'
        ),
        '{"schema_version":1,"study_id":NaN}',
        '{"schema_version":1,"study_id":Infinity}',
        '{"schema_version":1,"study_id":-Infinity}',
    ],
)
def test_parser_refuses_duplicate_keys_and_nonfinite_json_constants(document: str) -> None:
    with pytest.raises(ValueError, match=r"duplicate|finite|valid"):
        parse_spatial_study_definition_json(document)


def test_parser_rejects_invalid_utf8_and_secret_input_without_echo() -> None:
    with pytest.raises(ValueError, match=r"UTF-8|JSON"):
        parse_spatial_study_definition_json(b"\xff\xfe")

    secret = "sk-live-abcdefghij"
    document = _definition_document()
    document["study_id"] = secret
    with pytest.raises((ValidationError, ValueError)) as error:
        parse_spatial_study_definition_json(json.dumps(document).encode("utf-8"))
    assert secret not in str(error.value)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_parser_refuses_complete_non_utf8_byte_documents(encoding: str) -> None:
    document = json.dumps(_definition_document()).encode(encoding)

    with pytest.raises(ValueError, match="UTF-8"):
        parse_spatial_study_definition_json(document)


def test_parser_refuses_credential_shaped_unknown_keys_without_echo() -> None:
    secret_key = "sk-live-abcdefghij"
    document = _definition_document() | {secret_key: "value"}

    with pytest.raises(ValueError, match="extra") as error:
        parse_spatial_study_definition_json(json.dumps(document))

    assert secret_key not in str(error.value)


def test_parser_enforces_the_shared_json_nesting_ceiling_before_schema_validation() -> None:
    nested: object = "leaf"
    for _ in range(40):
        nested = [nested]
    document = _definition_document()
    document["pairs"] = nested

    with pytest.raises(ValueError, match="nest"):
        parse_spatial_study_definition_json(json.dumps(document))


def test_parser_does_not_mutate_the_callers_python_document() -> None:
    document = _definition_document()
    original = deepcopy(document)

    parse_spatial_study_definition_json(json.dumps(document))

    assert document == original
