"""Journey authoring, protected assertions and deterministic compilation.

The negative tests are the point of this module: every one corresponds to a way a journey could make
its own success trivial, hand the navigator something it must not have, or reach outside its
sandbox.

Requirements: FR-003, FR-005, FR-007, FR-023.
Invariants: INV-01, INV-02, INV-03, INV-04, INV-05, INV-08, INV-16.
"""

from __future__ import annotations

import dataclasses

import pytest

from accessforge_domain.journeys import (
    ASSERTION_OBSERVERS,
    ActionBudget,
    Assertion,
    AssertionKind,
    AssertionSet,
    CapabilityError,
    FixtureBinding,
    JourneyDraft,
    JourneyError,
    Observer,
    TaskIntent,
    UnknownReason,
    compile_journey,
    validate_draft,
)

# --- the E0 journey: form-error recovery ------------------------------------------------------


def e0_assertions() -> AssertionSet:
    """The real E0 scenario's truth conditions.

    Note what is *not* here: no expected action sequence. A journey that specified the keystrokes
    would be a replay of a known-good path, and passing it would say nothing about whether a real
    reader user could accomplish the task.
    """
    return AssertionSet(
        (
            Assertion(
                assertion_id="completion.one-request",
                kind=AssertionKind.TASK_COMPLETION,
                description="Exactly one service request exists for this fixture instance.",
                unknown_reasons=frozenset({UnknownReason.OBSERVER_UNREACHABLE}),
            ),
            Assertion(
                assertion_id="announcement.validation-error",
                kind=AssertionKind.REQUIRED_ANNOUNCEMENT,
                description="The validation error for the email field is announced to the reader.",
                unknown_reasons=frozenset(
                    {
                        UnknownReason.READER_UNAVAILABLE,
                        UnknownReason.OBSERVATION_MISSING,
                        UnknownReason.AMBIGUOUS_LANGUAGE,
                    }
                ),
            ),
            Assertion(
                assertion_id="focus.moves-to-invalid-field",
                kind=AssertionKind.FOCUS_BEHAVIOUR,
                description="Focus moves to the first invalid field after submission.",
                unknown_reasons=frozenset(
                    {UnknownReason.READER_UNAVAILABLE, UnknownReason.OBSERVATION_MISSING}
                ),
            ),
            Assertion(
                assertion_id="validation.still-rejects-bad-email",
                kind=AssertionKind.FUNCTIONAL_VALIDATION,
                description="Server-side validation still rejects a malformed email address.",
                unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
            ),
            Assertion(
                assertion_id="effects.no-external-submission",
                kind=AssertionKind.FORBIDDEN_EFFECT,
                description="No email, payment or external submission occurred.",
                unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
            ),
        )
    )


def e0_draft(**over: object) -> JourneyDraft:
    defaults: dict[str, object] = {
        "name": "form-error-recovery",
        "intent": TaskIntent(
            summary="Submit a service request for a keyboard accessibility problem.",
            start_url="http://127.0.0.1:8081/form/FIXTURE",
            success_condition="Exactly one request is recorded after correcting the email address.",
        ),
        "assertions": e0_assertions(),
        "fixture": FixtureBinding(
            template_id="service-request-v1",
            navigator_values={
                "full_name": "Test Person",
                "email_invalid": "not-an-email",
                "email_valid": "test.person@example.test",
                "category": "access-request",
                "description": "Cannot reach the settings page using the keyboard.",
            },
            reset_values={"seed": "service-request-v1"},
            observer_config={"expected_request_count": "1"},
        ),
        "budget": ActionBudget(max_actions=120, wall_time_seconds=600),
        "platform": "darwin",
        "allowed_actions": frozenset(
            {
                "NEXT",
                "PREVIOUS",
                "ACTIVATE",
                "TYPE_TEXT",
                "KEY_CHORD",
                "READ_CURRENT",
                "WAIT_FOR_READER_IDLE",
                "STOP",
            }
        ),
        "allowed_key_chords": frozenset({"TAB", "SHIFT+TAB", "ENTER"}),
        "allowed_effects": frozenset({"FIXTURE_SUBMIT", "FIXTURE_RESET"}),
    }
    return JourneyDraft(**{**defaults, **over})  # type: ignore[arg-type]


def test_the_e0_journey_compiles() -> None:
    # Allowed-path control: a validator that refused everything would fail here.
    compiled = compile_journey(e0_draft(), version_id="v1")
    assert len(compiled.version.journey_digest) == 64
    assert len(compiled.version.assertion_set_digest) == 64
    assert len(compiled.version.fixture_digest) == 64
    assert len(compiled.version.navigator_policy_digest) == 64


def test_compilation_is_deterministic() -> None:
    """The same reviewed journey must compile identically, so two runs can be shown to share it."""
    first = compile_journey(e0_draft(), version_id="v1")
    second = compile_journey(e0_draft(), version_id="v2")
    assert first.version.journey_digest == second.version.journey_digest
    assert first.version.assertion_set_digest == second.version.assertion_set_digest
    assert first.version.fixture_digest == second.version.fixture_digest
    assert first.version.navigator_policy_digest == second.version.navigator_policy_digest


@pytest.mark.parametrize(
    "change",
    [
        {"name": "renamed"},
        {"budget": ActionBudget(max_actions=121, wall_time_seconds=600)},
        {"allowed_key_chords": frozenset({"TAB"})},
        {"allowed_effects": frozenset({"FIXTURE_SUBMIT"})},
        {"platform": "win32"},
    ],
)
def test_any_change_produces_a_new_journey_digest(change: dict) -> None:
    """Editing creates a new version, never an amended one."""
    if change.get("platform") == "win32":
        change = {**change, "allowed_key_chords": frozenset({"TAB", "ENTER"})}
    baseline = compile_journey(e0_draft()).version.journey_digest
    assert compile_journey(e0_draft(**change)).version.journey_digest != baseline


# --- the navigator boundary -------------------------------------------------------------------


def test_the_navigator_policy_contains_no_observer_or_oracle_material() -> None:
    """INV-01. The navigator receives task intent and safe values, nothing else.

    Asserted by searching the whole policy rather than checking named keys, so a future addition
    cannot slip answer-key material in under a different name.
    """
    compiled = compile_journey(e0_draft())
    rendered = repr(compiled.navigator_policy)

    assert "expected_request_count" not in rendered, "observer configuration reached the navigator"
    assert "seed" not in rendered, "reset material reached the navigator"
    for forbidden in ("querySelector", "#email", ".error", "xpath"):
        assert forbidden not in rendered

    # And what it does contain is exactly the navigator-visible fixture values.
    assert compiled.navigator_policy["fixtureValues"] == e0_draft().fixture.navigator_view()


def test_the_policy_states_its_own_prohibitions() -> None:
    """The prohibition travels with the policy rather than living only in a document."""
    policy = compile_journey(e0_draft()).navigator_policy
    forbidden = policy["forbiddenObservations"]
    assert isinstance(forbidden, list)
    for item in ("DOM", "SELECTORS", "SCREENSHOTS", "OBSERVER_RECEIPTS", "ASSERTION_EXPECTATIONS"):
        assert item in forbidden


@pytest.mark.parametrize(
    "sneaky",
    [
        "#email-error",
        ".validation-message",
        '[data-testid="submit"]',
        "//div[@id='error']",
        "document.querySelector('#email')",
        "input[name=email]",
    ],
)
def test_a_selector_cannot_be_smuggled_into_the_task_intent(sneaky: str) -> None:
    """A selector in the task payload hands the navigator the DOM its policy forbids."""
    with pytest.raises(JourneyError, match="selector or DOM expression"):
        TaskIntent(
            summary=f"Fix the thing at {sneaky}",
            start_url="http://127.0.0.1:8081/form/X",
            success_condition="One request recorded.",
        )


def test_a_selector_cannot_be_smuggled_into_a_navigator_fixture_value() -> None:
    with pytest.raises(JourneyError, match="looks like a selector"):
        FixtureBinding(template_id="t", navigator_values={"target": "#email-error"})


# Three of the shapes below are exactly what the repository's own secret scanner looks for, so
# writing them as literals would make this file trip that scanner on every push. They are assembled
# from fragments instead: the value handed to the assertion is byte-identical to the real shape, and
# no line of this file matches the scanner's patterns. Weakening the scanner to accommodate a test
# fixture would have been the wrong trade.
_AWS_KEY_SHAPE = "AKI" + "A" + "IOSFODNN7" + "EXAMPLE"
_GITHUB_PAT_SHAPE = "gh" + "p_" + ("abcdefghijklmnopqrstuvwxyz" + "0123456789")
_PEM_HEADER_SHAPE = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"


@pytest.mark.parametrize(
    "secret",
    [
        pytest.param(_AWS_KEY_SHAPE, id="aws-access-key-id"),
        pytest.param(_GITHUB_PAT_SHAPE, id="github-personal-access-token"),
        pytest.param(_PEM_HEADER_SHAPE, id="pem-private-key-header"),
        pytest.param("password: hunter2", id="labelled-password"),
        pytest.param("api_key=abcdef", id="labelled-api-key"),
    ],
)
def test_a_secret_cannot_be_placed_in_a_navigator_fixture_value(secret: str) -> None:
    """Fixture values are synthetic. A secret here would travel into every citing export."""
    with pytest.raises(JourneyError, match="looks like a credential"):
        FixtureBinding(template_id="t", navigator_values={"v": secret})


def test_the_assembled_credential_fixtures_are_genuinely_credential_shaped() -> None:
    """Guards the fragmenting above.

    If a fragment were mistyped the test beside it would still pass for the wrong reason -- the
    value would simply stop being credential-shaped and the assertion would be proving nothing. The
    patterns here are the repository secret scanner's own, copied from `.github/workflows/ci.yml`.
    """
    import re

    scanner = re.compile(
        r"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    )
    for value in (_AWS_KEY_SHAPE, _GITHUB_PAT_SHAPE, _PEM_HEADER_SHAPE):
        assert scanner.search(value), f"{value!r} is no longer credential-shaped"


def test_observer_configuration_cannot_double_as_navigator_input() -> None:
    """The observer's expectations are the answer key."""
    with pytest.raises(JourneyError, match="answer key"):
        FixtureBinding(
            template_id="t",
            navigator_values={"expected": "1"},
            observer_config={"expected": "1"},
        )


# --- protected assertions ----------------------------------------------------------------------


def test_each_assertion_kind_has_exactly_one_entitled_observer() -> None:
    """A kind decidable by two observers would let the weaker answer for the stronger."""
    assert set(ASSERTION_OBSERVERS) == set(AssertionKind)
    assert len(ASSERTION_OBSERVERS) == len(AssertionKind)


def test_a_reader_cannot_decide_task_completion() -> None:
    """A phrase announced on screen is not evidence that a row was written."""
    completion = Assertion(
        assertion_id="c",
        kind=AssertionKind.TASK_COMPLETION,
        description="d",
        unknown_reasons=frozenset({UnknownReason.OBSERVER_UNREACHABLE}),
    )
    assert completion.observer is Observer.APPLICATION_OBSERVER
    assert completion.observer is not Observer.READER


def test_an_application_observer_cannot_decide_an_announcement() -> None:
    """A backend row existing proves nothing about what was spoken."""
    announcement = Assertion(
        assertion_id="a",
        kind=AssertionKind.REQUIRED_ANNOUNCEMENT,
        description="d",
        unknown_reasons=frozenset({UnknownReason.READER_UNAVAILABLE}),
    )
    assert announcement.observer is Observer.READER


def test_a_required_assertion_must_declare_how_it_can_be_unknown() -> None:
    """INV-02 at authoring time.

    A condition that can only be true or false cannot represent a missing observation, which is how
    an unobserved assertion silently becomes a reported defect.
    """
    with pytest.raises(ValueError, match="declares no way to be unknown"):
        Assertion(
            assertion_id="x",
            kind=AssertionKind.REQUIRED_ANNOUNCEMENT,
            description="d",
            required=True,
            unknown_reasons=frozenset(),
        )


def test_an_optional_assertion_need_not_declare_unknown_reasons() -> None:
    # Allowed-path control: the rule applies to required conditions, which gate the verdict.
    Assertion(assertion_id="x", kind=AssertionKind.READING_ORDER, description="d", required=False)


def test_a_journey_must_include_a_required_completion_assertion() -> None:
    """Required completion is a frozen condition, not an optional extra."""
    without = tuple(
        a for a in e0_assertions().assertions if a.kind is not AssertionKind.TASK_COMPLETION
    )
    with pytest.raises(ValueError, match="required TASK_COMPLETION"):
        AssertionSet(without)


def test_a_completion_assertion_marked_optional_does_not_satisfy_the_rule() -> None:
    relaxed = tuple(
        dataclasses.replace(a, required=False) if a.kind is AssertionKind.TASK_COMPLETION else a
        for a in e0_assertions().assertions
    )
    with pytest.raises(ValueError, match="required TASK_COMPLETION"):
        AssertionSet(relaxed)


def test_a_required_assertion_cannot_disappear_during_a_version_update() -> None:
    """Dropping an assertion changes the digest, so the change cannot pass as the same journey."""
    baseline = compile_journey(e0_draft())
    weakened = AssertionSet(
        tuple(
            a
            for a in e0_assertions().assertions
            if a.assertion_id != "announcement.validation-error"
        )
    )
    altered = compile_journey(e0_draft(assertions=weakened))
    assert altered.version.assertion_set_digest != baseline.version.assertion_set_digest
    assert altered.version.journey_digest != baseline.version.journey_digest


def test_assertions_are_frozen_against_in_place_edits() -> None:
    """INV-05: an agent holding an assertion cannot change what it asserts."""
    assertion = e0_assertions().assertions[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        assertion.required = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        assertion.description = "something easier"  # type: ignore[misc]


def test_duplicate_assertion_identifiers_are_refused() -> None:
    duplicate = e0_assertions().assertions + (e0_assertions().assertions[0],)
    with pytest.raises(ValueError, match="unique"):
        AssertionSet(duplicate)


# --- capability validation ----------------------------------------------------------------------


def test_an_unsupported_platform_is_refused_with_a_code() -> None:
    with pytest.raises(CapabilityError) as excinfo:
        validate_draft(e0_draft(platform="linux"))
    assert excinfo.value.code == "UNSUPPORTED_PLATFORM"


def test_an_action_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(CapabilityError) as excinfo:
        validate_draft(e0_draft(allowed_actions=frozenset({"NEXT", "CLICK_AT_COORDINATES"})))
    assert excinfo.value.code == "UNSUPPORTED_ACTION"


@pytest.mark.parametrize(
    "chord",
    ["CMD+T", "CMD+L", "CMD+OPT+I", "CMD+C", "CMD+Q", "CTRL+ALT+DELETE", "F12"],
)
def test_a_chord_that_escapes_the_browser_is_refused(chord: str) -> None:
    """Address bar, new tab, devtools, clipboard and the OS are all absent from the allowlist."""
    with pytest.raises(CapabilityError) as excinfo:
        validate_draft(e0_draft(allowed_key_chords=frozenset({"TAB", chord})))
    assert excinfo.value.code == "FORBIDDEN_KEY_CHORD"


def test_a_windows_chord_is_not_permitted_on_macos() -> None:
    """Platform allowlists are separate, not a union."""
    with pytest.raises(CapabilityError, match="FORBIDDEN_KEY_CHORD"):
        validate_draft(e0_draft(platform="darwin", allowed_key_chords=frozenset({"DOWN"})))


def test_listing_chords_without_the_key_chord_action_is_inconsistent() -> None:
    with pytest.raises(CapabilityError) as excinfo:
        validate_draft(
            e0_draft(
                allowed_actions=frozenset({"NEXT", "ACTIVATE", "TYPE_TEXT"}),
                allowed_key_chords=frozenset({"TAB"}),
            )
        )
    assert excinfo.value.code == "INCONSISTENT_CAPABILITY"


def test_typing_without_fixture_values_is_inconsistent() -> None:
    """A journey that may type arbitrary text is not bounded by its fixture."""
    with pytest.raises(CapabilityError, match="INCONSISTENT_CAPABILITY"):
        validate_draft(
            e0_draft(
                fixture=FixtureBinding(template_id="t", reset_values={"seed": "x"}),
                allowed_key_chords=frozenset(),
                allowed_actions=frozenset({"NEXT", "TYPE_TEXT"}),
            )
        )


@pytest.mark.parametrize(
    "effect", ["SEND_EMAIL", "TAKE_PAYMENT", "SUBMIT_APPLICATION", "PUBLISH_TO_GITHUB"]
)
def test_an_external_effect_cannot_be_authorized_by_a_journey(effect: str) -> None:
    with pytest.raises(CapabilityError) as excinfo:
        validate_draft(e0_draft(allowed_effects=frozenset({"FIXTURE_SUBMIT", effect})))
    assert excinfo.value.code == "FORBIDDEN_EFFECT"


def test_an_unbounded_budget_is_refused() -> None:
    with pytest.raises(JourneyError, match="open-ended licence"):
        ActionBudget(max_actions=10_000, wall_time_seconds=600)
    with pytest.raises(JourneyError, match="never ends"):
        ActionBudget(max_actions=100, wall_time_seconds=86_400)
    with pytest.raises(JourneyError):
        ActionBudget(max_actions=0, wall_time_seconds=600)


def test_a_journey_with_no_actions_is_refused() -> None:
    with pytest.raises(CapabilityError, match="NO_ACTIONS"):
        validate_draft(e0_draft(allowed_actions=frozenset(), allowed_key_chords=frozenset()))


# --- reviewer summary ---------------------------------------------------------------------------


def test_the_reviewer_summary_says_what_an_approval_permits_and_what_it_does_not() -> None:
    """An approval that does not state its exclusions is easy to over-read."""
    summary = compile_journey(e0_draft()).reviewer_summary
    excluded = summary["thisDoesNotPermit"]
    assert isinstance(excluded, list)
    joined = " ".join(excluded).lower()
    assert "patch" in joined
    assert "publish" in joined
    assert summary["effectsThisPermits"] == ["FIXTURE_RESET", "FIXTURE_SUBMIT"]
    assert "120 actions" in str(summary["boundedBy"])


def test_the_reviewer_summary_names_each_assertions_observer_and_unknown_cases() -> None:
    summary = compile_journey(e0_draft()).reviewer_summary
    required = summary["requiredAssertions"]
    assert isinstance(required, list)
    by_id = {r["id"]: r for r in required}
    assert by_id["completion.one-request"]["decidedBy"] == "APPLICATION_OBSERVER"
    assert by_id["announcement.validation-error"]["decidedBy"] == "READER"
    assert "READER_UNAVAILABLE" in by_id["announcement.validation-error"]["canBeUnknownWhen"]


def test_the_reviewer_summary_exposes_no_oracle_material() -> None:
    rendered = repr(compile_journey(e0_draft()).reviewer_summary)
    assert "expected_request_count" not in rendered


# --- fixture digest behaviour --------------------------------------------------------------------


def test_changing_an_oracle_value_changes_the_fixture_digest() -> None:
    """A different expectation is a different run, so the seal must not survive it."""
    baseline = compile_journey(e0_draft()).version.fixture_digest
    altered = compile_journey(
        e0_draft(
            fixture=FixtureBinding(
                template_id="service-request-v1",
                navigator_values=e0_draft().fixture.navigator_values,
                reset_values={"seed": "service-request-v1"},
                observer_config={"expected_request_count": "2"},
            )
        )
    ).version.fixture_digest
    assert altered != baseline


def test_the_fixture_digest_does_not_contain_oracle_values_in_clear() -> None:
    """The digest must change with the oracle without carrying it."""
    compiled = compile_journey(e0_draft())
    assert "expected_request_count" not in compiled.version.fixture_digest
    assert len(compiled.version.fixture_digest) == 64


def test_a_copied_announcement_phrase_is_not_an_observation() -> None:
    """A phrase appearing in a fixture description cannot count as actual reader output.

    Stated structurally: the expected announcement lives in an assertion description, which is
    reviewer-facing text, and nothing in the navigator policy or the fixture values contains it. The
    only way to satisfy the assertion is for the reader to actually say it.
    """
    compiled = compile_journey(e0_draft())
    phrase = "announced to the reader"
    assert any(phrase in a.description for a in e0_assertions().assertions)
    assert phrase not in repr(compiled.navigator_policy)
    assert phrase not in repr(compiled.version.draft.fixture.navigator_view())
