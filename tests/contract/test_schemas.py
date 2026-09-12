"""Schema validation at trust boundaries, and agreement between schemas and domain code.

Requirements: FR-002, FR-003, FR-016. Invariants: INV-03, INV-08.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import TypedDict

import pytest

from accessforge_contracts import (
    SchemaValidationError,
    available_schemas,
    schema_digest,
    validate,
)
from accessforge_contracts._generated import (
    APPROVAL_SCOPES,
    CONDITIONS,
    OUTCOMES,
    RUN_STATUSES,
    SCHEMA_DIGESTS,
)
from accessforge_domain.states import ApprovalScope, Condition, Outcome, RunStatus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads(
    (ROOT / "packages/contracts/fixtures/schema-fixtures.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("name", list(FIXTURES["valid"]))
def test_valid_fixtures_are_accepted(name: str) -> None:
    # Allowed-path control: a validator that rejected everything would fail here.
    validate(name, FIXTURES["valid"][name])


class InvalidCase(TypedDict):
    schema: str
    payload: object
    why: str


@pytest.mark.parametrize("case", FIXTURES["invalid"], ids=[c["why"] for c in FIXTURES["invalid"]])
def test_invalid_fixtures_are_rejected(case: InvalidCase) -> None:
    with pytest.raises(SchemaValidationError):
        validate(case["schema"], case["payload"])


def test_unknown_fields_are_rejected_on_security_sensitive_objects() -> None:
    """A tolerated unknown field is how an unrecognized authority claim rides along unnoticed."""
    manifest = dict(FIXTURES["valid"]["run-manifest.schema.json"])
    manifest["permittedEffectsOverride"] = ["ANYTHING"]
    with pytest.raises(SchemaValidationError, match="Additional properties|not allowed"):
        validate("run-manifest.schema.json", manifest)


def test_validation_reports_every_error_not_only_the_first() -> None:
    with pytest.raises(SchemaValidationError) as excinfo:
        validate("run-manifest.schema.json", {"schemaVersion": 1})
    assert len(excinfo.value.errors) > 1


# --- schemas and domain code must not drift apart -------------------------------------------


@pytest.mark.parametrize(
    ("enum_type", "generated"),
    [
        (RunStatus, RUN_STATUSES),
        (Outcome, OUTCOMES),
        (Condition, CONDITIONS),
        (ApprovalScope, APPROVAL_SCOPES),
    ],
)
def test_domain_enums_match_the_schema_derived_constants(
    enum_type: type[StrEnum], generated: tuple[str, ...]
) -> None:
    """The Python enums are hand-written; the constants are generated from the schemas.

    If someone adds a state to one and not the other, this fails rather than letting two
    definitions of the vocabulary coexist.
    """
    assert {member.value for member in enum_type} == set(generated)


def test_generated_digests_match_the_current_schemas() -> None:
    """Catches a schema edited without regenerating the bindings."""
    assert set(SCHEMA_DIGESTS) == set(available_schemas())
    for name, recorded in SCHEMA_DIGESTS.items():
        assert schema_digest(name) == recorded, (
            f"{name} has changed since bindings were generated; run "
            "`uv run python scripts/generate_contract_bindings.py`"
        )


def test_every_schema_forbids_unknown_top_level_fields() -> None:
    """Structural rule, checked once rather than remembered per schema."""
    from accessforge_contracts import load_schema

    for name in available_schemas():
        schema = load_schema(name)
        if schema.get("type") != "object":
            continue  # common.schema.json is a $defs library, not an object schema
        assert schema.get("additionalProperties") is False, f"{name} tolerates unknown fields"


def test_load_schema_returns_a_copy_the_caller_cannot_use_to_weaken_validation() -> None:
    """The cache is private.

    Handing out the cached object let a caller drop `additionalProperties` and weaken validation
    for the rest of the process.
    """
    from accessforge_contracts import load_schema

    schema = load_schema("run-manifest.schema.json")
    assert schema["additionalProperties"] is False
    schema["additionalProperties"] = True
    schema["properties"]["runId"] = {"type": "string"}

    assert load_schema("run-manifest.schema.json")["additionalProperties"] is False

    # And validation itself is unaffected by the attempted mutation.
    manifest = dict(FIXTURES["valid"]["run-manifest.schema.json"])
    manifest["injected"] = True
    with pytest.raises(SchemaValidationError):
        validate("run-manifest.schema.json", manifest)
