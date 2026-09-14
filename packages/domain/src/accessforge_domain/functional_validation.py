"""Versioned protected reference validation inputs; never navigator configuration."""

from typing import Any

from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST

from .canonical import digest

VALID_VALUES = (
    ("full_name", "Test Person"),
    ("email", "test.person@example.test"),
    ("category", "access-request"),
    ("description", "Keyboard access request for testing."),
)
INVALID_VALUES = (
    ("email", "not-an-email"),
    ("full_name", ""),
    ("category", "forbidden"),
    ("description", "short"),
)


def validation_contract() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "fixtureTemplateDigest": REFERENCE_FIXTURE_DIGEST,
        "validValues": dict(VALID_VALUES),
        "invalidCases": [
            {"field": field, "value": value, "httpStatus": 422, "persistedRequests": 0}
            for field, value in INVALID_VALUES
        ],
    }


VALIDATION_SUITE_DIGEST = digest(validation_contract())
