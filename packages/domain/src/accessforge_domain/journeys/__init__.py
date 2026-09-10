"""Versioned journeys, protected assertions and compilation.

A journey is the reviewed statement of what a run is trying to do and what would count as success.
The whole point is that neither the navigator nor the repair agent can change those criteria: a
repair that makes the test easier is not a repair (INV-16), and an agent that could edit an
assertion could certify its own work (INV-05).

So everything here is immutable once compiled, and the separation of three kinds of material is
structural rather than conventional:

* **safe navigator input** — the task intent and the fixture values a person would type
* **trusted fixture reset data** — what the setup identity uses to prepare a fresh instance
* **privileged observer configuration** — how the independent observer checks the application

A value in the wrong one of those is the bug this module exists to prevent.
"""

from .assertions import (
    ASSERTION_OBSERVERS,
    Assertion,
    AssertionKind,
    AssertionSet,
    Observer,
    UnknownReason,
)
from .compile import CompiledJourney, compile_journey
from .dsl import (
    ActionBudget,
    FixtureBinding,
    JourneyDraft,
    JourneyError,
    JourneyVersion,
    TaskIntent,
)
from .validation import CapabilityError, validate_draft

__all__ = [
    "ASSERTION_OBSERVERS",
    "ActionBudget",
    "Assertion",
    "AssertionKind",
    "AssertionSet",
    "CapabilityError",
    "CompiledJourney",
    "FixtureBinding",
    "JourneyDraft",
    "JourneyError",
    "JourneyVersion",
    "Observer",
    "TaskIntent",
    "UnknownReason",
    "compile_journey",
    "validate_draft",
]
