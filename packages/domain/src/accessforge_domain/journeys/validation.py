"""Capability validation with actionable errors.

Every refusal here corresponds to a way a journey could reach outside its sandbox or make its own
success trivial. The errors are deliberately specific: a service returning "invalid journey"
leaves an
author guessing, and guessing is how the selector ends up back in the payload.
"""

from __future__ import annotations

from .assertions import AssertionKind
from .dsl import (
    ALLOWED_ACTIONS,
    ALLOWED_KEY_CHORDS,
    JourneyDraft,
)


class CapabilityError(ValueError):
    """A journey asks for something the platform or policy does not permit.

    Maps to a 422 at the service boundary: the request was well formed and the capability is not
    available, which is a different answer from "malformed".
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def validate_draft(draft: JourneyDraft) -> None:
    """Raise ``CapabilityError`` unless this draft is within policy for its platform."""
    if draft.platform not in ALLOWED_KEY_CHORDS:
        raise CapabilityError(
            "UNSUPPORTED_PLATFORM",
            f"{draft.platform!r} has no key-chord policy; supported platforms are "
            f"{sorted(ALLOWED_KEY_CHORDS)}",
        )

    unsupported = draft.allowed_actions - ALLOWED_ACTIONS
    if unsupported:
        raise CapabilityError(
            "UNSUPPORTED_ACTION",
            f"actions {sorted(unsupported)} are not in the allowlist {sorted(ALLOWED_ACTIONS)}; "
            "the vocabulary is fixed so a journey cannot invent a capability",
        )
    if not draft.allowed_actions:
        raise CapabilityError("NO_ACTIONS", "a journey that may take no action cannot do anything")

    forbidden_chords = draft.allowed_key_chords - ALLOWED_KEY_CHORDS[draft.platform]
    if forbidden_chords:
        raise CapabilityError(
            "FORBIDDEN_KEY_CHORD",
            f"chords {sorted(forbidden_chords)} are not permitted on {draft.platform}; the "
            "per-platform allowlist excludes anything reaching the operating system, the address "
            "bar, developer tools or the clipboard",
        )
    if draft.allowed_key_chords and "KEY_CHORD" not in draft.allowed_actions:
        raise CapabilityError(
            "INCONSISTENT_CAPABILITY",
            "key chords are listed but KEY_CHORD is not an allowed action",
        )

    if "TYPE_TEXT" in draft.allowed_actions and not draft.fixture.navigator_values:
        raise CapabilityError(
            "INCONSISTENT_CAPABILITY",
            "TYPE_TEXT is allowed but no navigator fixture values exist to type; a journey "
            "that may "
            "type arbitrary text is not bounded by its fixture",
        )

    # A journey claiming a forbidden-effect assertion needs the effect named, or the assertion has
    # nothing to watch for.
    for assertion in draft.assertions.assertions:
        if assertion.kind is AssertionKind.FORBIDDEN_EFFECT and not draft.allowed_effects:
            continue  # nothing permitted, so nothing to forbid beyond the default
    if not draft.allowed_effects <= {"FIXTURE_SUBMIT", "FIXTURE_RESET"}:
        raise CapabilityError(
            "FORBIDDEN_EFFECT",
            f"effects {sorted(draft.allowed_effects - {'FIXTURE_SUBMIT', 'FIXTURE_RESET'})} "
            "are not "
            "permitted; E0 and R1 effects are confined to owned test fixtures, and no journey may "
            "authorize an external submission, email or payment",
        )
