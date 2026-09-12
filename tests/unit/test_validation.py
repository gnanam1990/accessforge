"""Server-side validation is the behaviour a frontend repair must not weaken."""

from __future__ import annotations

import pytest

from reference_app.validation import (
    ALLOWED_CATEGORIES,
    MAX_DESCRIPTION,
    MIN_DESCRIPTION,
    FieldError,
    validate_service_request,
)

VALID = {
    "full_name": "Test Person",
    "email": "test.person@example.com",
    "category": "access-request",
    "description": "Cannot reach the settings page using the keyboard.",
}


def _fields(errors: list[FieldError]) -> set[str]:
    return {e.field for e in errors}


def test_allowed_control_passes() -> None:
    # An always-deny implementation must fail this.
    assert validate_service_request(**VALID) == []


@pytest.mark.parametrize("category", ALLOWED_CATEGORIES)
def test_every_allowed_category_is_accepted(category: str) -> None:
    assert validate_service_request(**{**VALID, "category": category}) == []


@pytest.mark.parametrize(
    "email",
    ["", "  ", "no-at-sign", "missing@domain", "@example.com", "a@b", "spaces in@example.com"],
)
def test_malformed_emails_are_rejected(email: str) -> None:
    assert "email" in _fields(validate_service_request(**{**VALID, "email": email}))


@pytest.mark.parametrize("category", ["", "unknown", "ACCESS-REQUEST", "other; drop"])
def test_unknown_categories_are_rejected(category: str) -> None:
    assert "category" in _fields(validate_service_request(**{**VALID, "category": category}))


@pytest.mark.parametrize(
    ("length", "expected_error"),
    [
        (MIN_DESCRIPTION - 1, True),
        (MIN_DESCRIPTION, False),
        (MAX_DESCRIPTION, False),
        (MAX_DESCRIPTION + 1, True),
    ],
)
def test_description_boundaries(length: int, expected_error: bool) -> None:
    errors = validate_service_request(**{**VALID, "description": "x" * length})
    assert ("description" in _fields(errors)) is expected_error


def test_all_errors_are_returned_not_only_the_first() -> None:
    errors = validate_service_request(full_name="", email="bad", category="nope", description="")
    assert _fields(errors) == {"full_name", "email", "category", "description"}


def test_whitespace_only_name_is_rejected() -> None:
    assert "full_name" in _fields(validate_service_request(**{**VALID, "full_name": "   "}))
