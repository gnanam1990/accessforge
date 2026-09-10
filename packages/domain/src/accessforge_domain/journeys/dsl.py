"""The journey DSL: what a run is trying to do, bounded.

Everything is immutable and explicitly bounded. An unbounded wait or an unlimited action count is
not
a journey, it is an open-ended licence to drive someone's desktop, so budgets are required
rather than
defaulted.

The type split is the security boundary. ``FixtureBinding`` separates three kinds of material, and a
value in the wrong one is the defect this module exists to prevent:

* ``navigator_values`` — what a person would type. The navigator sees only these.
* ``reset_values`` — what the setup identity uses to prepare a fresh instance.
* ``observer_config`` — how the independent observer checks the application. Never reaches the
  navigator, because it is the answer key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .assertions import AssertionSet

# The complete allowlist from CONTRACTS section 6. These are AccessForge abstractions, not
# screen-reader library method names.
ALLOWED_ACTIONS = frozenset(
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
)

# Per-platform key chords, narrowly scoped to reader and browser task navigation. Anything that
# reaches the operating system, the address bar, devtools or the clipboard is absent on purpose.
ALLOWED_KEY_CHORDS = {
    "darwin": frozenset(
        {"TAB", "SHIFT+TAB", "ENTER", "SPACE", "ESCAPE", "CTRL+OPT+RIGHT", "CTRL+OPT+LEFT"}
    ),
    "win32": frozenset({"TAB", "SHIFT+TAB", "ENTER", "SPACE", "ESCAPE", "DOWN", "UP"}),
}

MAX_ACTIONS = 500
MAX_WALL_TIME_SECONDS = 1800

# Patterns that suggest a value is implementation detail rather than something a person would type.
# A selector in a task payload means the navigator is being handed the DOM, which its policy
# forbids.
_SELECTOR_SHAPES = (
    # An id or class selector anywhere in the text, not only at the start — the interesting case is
    # "fix the thing at #email-error", buried mid-sentence.
    #
    # The condition is stated as "not preceded by a word character" rather than "preceded by
    # whitespace or start". Requiring whitespace was the second wrong version of this rule: it kept
    # ordinary prose out, but it also let a selector through whenever punctuation sat in front of
    # it -- `(#email)`, `target:#email`, `"#email"`, `at:.email-error` -- which is how a selector
    # would plausibly be written in the first place. A negative lookbehind covers every one of those
    # and still excludes the prose the whitespace rule was protecting, because `example.test` and
    # `e.g.` have the sigil preceded by a letter.
    re.compile(r"(?<![\w-])[#.][A-Za-z_][\w-]*"),
    re.compile(r"\[[A-Za-z-]+\s*=\s*['\"]"),  # [data-testid="x"]
    re.compile(r"\b(?:div|span|input|button|form)\s*[>.#\[]"),  # css combinators
    re.compile(r"^\s*//|^\s*/html", re.IGNORECASE),  # xpath
    re.compile(r"\bdocument\.querySelector"),
)

# Shapes that look like credentials. Fixture values are synthetic by definition, so anything
# matching
# these is either a mistake or an attempt to smuggle a secret into an exportable artifact.
_SECRET_SHAPES = (
    re.compile(r"^(?:secretref://)?[A-Za-z0-9+/]{40,}={0,2}$"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:password|passwd|api[_-]?key|secret|token)\s*[:=]", re.IGNORECASE),
)


class JourneyError(ValueError):
    """A journey could not be authored as written."""


@dataclass(frozen=True, slots=True)
class ActionBudget:
    """Bounds on what a run may do. Required, never defaulted to unlimited."""

    max_actions: int
    wall_time_seconds: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_actions <= MAX_ACTIONS:
            raise JourneyError(
                f"max_actions must be between 1 and {MAX_ACTIONS}; an unbounded action count is an "
                "open-ended licence to drive a desktop"
            )
        if not 1 <= self.wall_time_seconds <= MAX_WALL_TIME_SECONDS:
            raise JourneyError(
                f"wall_time_seconds must be between 1 and {MAX_WALL_TIME_SECONDS}; "
                "an unbounded wait "
                "never ends and never reports why"
            )


@dataclass(frozen=True, slots=True)
class TaskIntent:
    """What a person is trying to accomplish, in their words.

    This is navigator input, so it must read as a goal rather than as instructions for reaching it.
    A step-by-step transcript here would make the run a replay of a known-good path instead of an
    attempt, and a journey that can only pass by following a script proves nothing about whether a
    real user could.
    """

    summary: str
    start_url: str
    success_condition: str

    def __post_init__(self) -> None:
        for name, value in (
            ("summary", self.summary),
            ("success_condition", self.success_condition),
        ):
            if not value.strip():
                raise JourneyError(f"task intent needs a {name}")
        for shape in _SELECTOR_SHAPES:
            for name, value in (
                ("summary", self.summary),
                ("success_condition", self.success_condition),
            ):
                if shape.search(value):
                    raise JourneyError(
                        f"task {name} contains what looks like a selector or DOM expression; the "
                        "navigator is not given the DOM, and a selector here would hand it one"
                    )


@dataclass(frozen=True, slots=True)
class FixtureBinding:
    """Fixture material, split by who may see it.

    The split is the security boundary. ``observer_config`` is the answer key: if it reached the
    navigator, the navigator could satisfy an assertion by reading what the assertion expects rather
    than by driving the application.
    """

    template_id: str
    navigator_values: Mapping[str, str] = field(default_factory=dict)
    reset_values: Mapping[str, str] = field(default_factory=dict)
    observer_config: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.template_id.strip():
            raise JourneyError("a fixture binding needs a template identifier")

        # Defensive immutable copies, taken before validation runs.
        #
        # `frozen=True` freezes the *fields*, not what they point at. Without this a caller keeps a
        # reference to the dictionary it passed in and can mutate it afterwards -- inserting the
        # credential or selector the checks below just rejected, or editing the oracle after the
        # fixture digest has been taken. The copy is first so that everything validated below is the
        # snapshot that is actually kept, and `MappingProxyType` over that copy means the stored
        # mapping cannot be written through either.
        for name in ("navigator_values", "reset_values", "observer_config"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        for key, value in self.navigator_values.items():
            for shape in _SECRET_SHAPES:
                if shape.search(value):
                    raise JourneyError(
                        f"navigator fixture value {key!r} looks like a credential; fixture "
                        "values are "
                        "synthetic, and a secret here would travel into every export that cites it"
                    )
            for shape in _SELECTOR_SHAPES:
                if shape.search(value):
                    raise JourneyError(
                        f"navigator fixture value {key!r} looks like a selector; the "
                        "navigator types "
                        "values, it does not receive implementation detail"
                    )
        overlap = set(self.navigator_values) & set(self.observer_config)
        if overlap:
            raise JourneyError(
                f"keys {sorted(overlap)} appear in both navigator values and observer config; the "
                "observer's expectations are the answer key and must not be navigator input"
            )

    def navigator_view(self) -> dict[str, str]:
        """Exactly what the navigator receives. Nothing else is reachable from here.

        A fresh dict rather than the stored mapping: the caller owns what it gets back, and nothing
        it does to that copy can reach the sealed binding.
        """
        return dict(self.navigator_values)


@dataclass(frozen=True, slots=True)
class JourneyDraft:
    """A journey before validation and compilation."""

    name: str
    intent: TaskIntent
    assertions: AssertionSet
    fixture: FixtureBinding
    budget: ActionBudget
    platform: str
    allowed_actions: frozenset[str]
    allowed_key_chords: frozenset[str]
    allowed_effects: frozenset[str]


@dataclass(frozen=True, slots=True)
class JourneyVersion:
    """An immutable, compiled journey version.

    Editing produces a new version with a new digest. There is no in-place edit, because a journey
    whose success criteria could change mid-run would make every outcome provisional.
    """

    version_id: str
    draft: JourneyDraft
    journey_digest: str
    assertion_set_digest: str
    fixture_digest: str
    navigator_policy_digest: str
