"""Compiling a journey draft into sealed, digested inputs.

Compilation is deterministic: the same reviewed draft produces the same four digests every time,
which
is what lets a baseline and a candidate be shown to share their journey exactly.

It also produces the **navigator policy** — the sealed statement of what the navigator may see and
do.
That policy is derived here rather than assembled at dispatch, so there is no later opportunity to
widen it, and its digest is bound into the run manifest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from accessforge_domain.canonical import digest

from .dsl import JourneyDraft, JourneyVersion
from .validation import validate_draft


@dataclass(frozen=True, slots=True)
class CompiledJourney:
    version: JourneyVersion
    navigator_policy: dict[str, object]
    """What the navigator is permitted to observe and do.

    Contains no fixture oracle material, no selectors and no observer configuration — a reader of
    this
    dictionary sees everything the navigator sees, which makes the boundary auditable rather than
    assumed.
    """

    reviewer_summary: dict[str, object]
    """What approving this journey actually permits, in reviewable terms."""


def compile_journey(draft: JourneyDraft, *, version_id: str | None = None) -> CompiledJourney:
    """Validate and compile a draft. Deterministic for a given draft."""
    validate_draft(draft)

    assertion_set_digest = digest(draft.assertions.canonical_form())

    # The fixture digest covers the template and the navigator-visible values, plus the *names* of
    # reset and observer keys but not their values. Changing an oracle value changes the run's
    # behaviour and must invalidate the seal; including the value itself would put answer-key
    # material
    # into a digest that travels with exports.
    fixture_digest = digest(
        {
            "templateId": draft.fixture.template_id,
            "navigatorValues": dict(sorted(draft.fixture.navigator_values.items())),
            "resetKeys": sorted(draft.fixture.reset_values),
            "observerKeys": sorted(draft.fixture.observer_config),
            "resetValuesDigest": digest(dict(sorted(draft.fixture.reset_values.items()))),
            "observerConfigDigest": digest(dict(sorted(draft.fixture.observer_config.items()))),
        }
    )

    navigator_policy: dict[str, object] = {
        "taskSummary": draft.intent.summary,
        "successCondition": draft.intent.success_condition,
        "startUrl": draft.intent.start_url,
        "allowedActions": sorted(draft.allowed_actions),
        "allowedKeyChords": sorted(draft.allowed_key_chords),
        "maxActions": draft.budget.max_actions,
        "wallTimeSeconds": draft.budget.wall_time_seconds,
        "fixtureValues": draft.fixture.navigator_view(),
        # Stated in the policy itself so the prohibition travels with it rather than living only in
        # a document someone has to remember.
        "forbiddenObservations": [
            "DOM",
            "SELECTORS",
            "SCREENSHOTS",
            "SOURCE",
            "OBSERVER_RECEIPTS",
            "ASSERTION_EXPECTATIONS",
        ],
    }
    navigator_policy_digest = digest(navigator_policy)

    journey_digest = digest(
        {
            "name": draft.name,
            "platform": draft.platform,
            "intent": {
                "summary": draft.intent.summary,
                "startUrl": draft.intent.start_url,
                "successCondition": draft.intent.success_condition,
            },
            "assertionSetDigest": assertion_set_digest,
            "fixtureDigest": fixture_digest,
            "navigatorPolicyDigest": navigator_policy_digest,
            "allowedEffects": sorted(draft.allowed_effects),
            "budget": {
                "maxActions": draft.budget.max_actions,
                "wallTimeSeconds": draft.budget.wall_time_seconds,
            },
        }
    )

    reviewer_summary: dict[str, object] = {
        "journey": draft.name,
        "platform": draft.platform,
        "whatItWillTry": draft.intent.summary,
        "whatCountsAsSuccess": draft.intent.success_condition,
        # The description is the truth condition -- the sentence that says what must be announced,
        # or what counts as the task being complete. Without it a reviewer sees an identifier, a
        # kind and an observer and has to go and read the source to learn what they are approving,
        # which is not a review of the assertions but a review of their names.
        "requiredAssertions": [
            {
                "id": a.assertion_id,
                "kind": a.kind.value,
                "mustBeTrue": a.description,
                "decidedBy": a.observer.value,
                "canBeUnknownWhen": sorted(r.value for r in a.unknown_reasons),
            }
            for a in draft.assertions.required
        ],
        "effectsThisPermits": sorted(draft.allowed_effects),
        "boundedBy": f"{draft.budget.max_actions} actions, {draft.budget.wall_time_seconds}s",
        # Spelled out because an approval that does not say what it excludes is easy to over-read.
        "thisDoesNotPermit": [
            "applying a patch",
            "publishing to a repository",
            "any effect outside the listed ones",
            "navigating outside the environment's allowed origins",
        ],
    }

    version = JourneyVersion(
        version_id=version_id or str(uuid.uuid4()),
        draft=draft,
        journey_digest=journey_digest,
        assertion_set_digest=assertion_set_digest,
        fixture_digest=fixture_digest,
        navigator_policy_digest=navigator_policy_digest,
    )
    return CompiledJourney(
        version=version,
        navigator_policy=navigator_policy,
        reviewer_summary=reviewer_summary,
    )
