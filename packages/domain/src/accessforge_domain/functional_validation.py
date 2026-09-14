"""Versioned protected reference validation inputs; never navigator configuration."""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ValidationObservation:
    """Measurements supplied by the protected driver, never candidate stdout or check names."""

    suite_digest: str
    cases: tuple[tuple[str, int, int], ...]  # field, HTTP status, independently observed row count

    def __post_init__(self) -> None:
        if (
            self.suite_digest != VALIDATION_SUITE_DIGEST
            or type(self.cases) is not tuple
            or len(self.cases) != len(INVALID_VALUES)
            or any(
                type(case) is not tuple
                or len(case) != 3
                or case[0] != expected[0]
                or type(case[1]) is not int
                or not 100 <= case[1] <= 599
                or type(case[2]) is not int
                or case[2] < 0
                for case, expected in zip(self.cases, INVALID_VALUES, strict=True)
            )
        ):
            raise ValueError(
                "validation observation requires the exact suite and complete measurements"
            )

    @property
    def passed(self) -> bool:
        return all(status == 422 and count == 0 for _, status, count in self.cases)

    def canonical_form(self) -> dict[str, Any]:
        return {
            "suiteDigest": self.suite_digest,
            "cases": [
                {"field": field, "httpStatus": status, "persistedRequests": count}
                for field, status, count in self.cases
            ],
        }
