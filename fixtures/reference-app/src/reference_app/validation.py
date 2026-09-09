"""Server-side validation.

This is the part a frontend repair must not remove. The accessibility defect injected into the
inaccessible variant is presentational: it changes how an error is announced, never whether the
backend rejects bad input. A candidate patch that made this validation more permissive to turn a
run green would be a regression, not a repair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALLOWED_CATEGORIES = ("access-request", "hardware", "software", "other")

# Deliberately simple and explicit: this is a fixture, and a surprising regex would make failures
# hard to attribute to the journey rather than to the fixture.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

MIN_DESCRIPTION = 10
MAX_DESCRIPTION = 2000
MAX_NAME = 200


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


def validate_service_request(
    *, full_name: str, email: str, category: str, description: str
) -> list[FieldError]:
    """Return every field error, not just the first.

    Returning all of them matters for the journey: a reader user correcting one field at a time
    should not have to rediscover the form's state on each attempt.
    """
    errors: list[FieldError] = []

    name = full_name.strip()
    if not name:
        errors.append(FieldError("full_name", "Full name is required."))
    elif len(name) > MAX_NAME:
        errors.append(FieldError("full_name", f"Full name must be {MAX_NAME} characters or fewer."))

    address = email.strip()
    if not address:
        errors.append(FieldError("email", "Email address is required."))
    elif not _EMAIL.match(address):
        errors.append(FieldError("email", "Enter an email address in the format name@example.com."))

    if category not in ALLOWED_CATEGORIES:
        errors.append(FieldError("category", f"Choose one of: {', '.join(ALLOWED_CATEGORIES)}."))

    body = description.strip()
    if len(body) < MIN_DESCRIPTION:
        errors.append(
            FieldError("description", f"Description must be at least {MIN_DESCRIPTION} characters.")
        )
    elif len(body) > MAX_DESCRIPTION:
        errors.append(
            FieldError("description", f"Description must be {MAX_DESCRIPTION} characters or fewer.")
        )

    return errors
