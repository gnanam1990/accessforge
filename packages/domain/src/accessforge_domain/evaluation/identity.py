"""Identity revalidation: does this evidence describe the thing that was approved?

Task 1 of the module prompt, and it comes first for a reason it states plainly: "Reject missing or
changed identities **before considering favorable observations**."

The ordering is the whole design. An evaluator that looked at the assertions first and the
identities second would compute PASS and then decide whether to believe it, and the natural
failure of that shape is a plausible-looking result reported against the wrong build. Here, an
identity mismatch never reaches the truth table at all.

Every identity is required. A missing one is not a mismatch to shrug at -- it is the absence of a
binding INV-03 requires to exist, and "we did not record which evaluator version produced this" is
indistinguishable from "any evaluator version could have".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IdentityKind(StrEnum):
    """The identities an outcome binds. Closed, because INV-03 enumerates them.

    A caller cannot add one, which matters more than it looks: an evaluator that accepted arbitrary
    identity names would let a producer bind an outcome to identities nobody requires, and the set
    that must match would become whatever the producer chose to send.
    """

    SOURCE = "SOURCE"
    BUILD = "BUILD"
    ENVIRONMENT = "ENVIRONMENT"
    RUNNER_PROFILE = "RUNNER_PROFILE"
    EVALUATOR = "EVALUATOR"
    MODEL = "MODEL"
    JOURNEY_VERSION = "JOURNEY_VERSION"
    ASSERTION_SET = "ASSERTION_SET"
    FIXTURE_INSTANCE = "FIXTURE_INSTANCE"


#: Every identity except MODEL must be present and match.
#:
#: MODEL is excluded because a run driven by a scripted navigator legitimately has no model, and
#: requiring one would make an unmodelled run permanently INCONCLUSIVE. When a model *was* used it
#: is required to match -- see `revalidate`, which treats a supplied-but-different model as a
#: mismatch rather than ignoring it.
ALWAYS_REQUIRED: frozenset[IdentityKind] = frozenset(
    {
        IdentityKind.SOURCE,
        IdentityKind.BUILD,
        IdentityKind.ENVIRONMENT,
        IdentityKind.RUNNER_PROFILE,
        IdentityKind.EVALUATOR,
        IdentityKind.JOURNEY_VERSION,
        IdentityKind.ASSERTION_SET,
        IdentityKind.FIXTURE_INSTANCE,
    }
)


@dataclass(frozen=True, slots=True)
class IdentityCheck:
    kind: IdentityKind
    sealed: str | None
    observed: str | None

    @property
    def matches(self) -> bool:
        return self.sealed is not None and self.sealed == self.observed


@dataclass(frozen=True, slots=True)
class IdentityVerdict:
    bound: bool
    reasons: tuple[str, ...] = ()


def revalidate(
    sealed: dict[IdentityKind, str], observed: dict[IdentityKind, str]
) -> IdentityVerdict:
    """Compare the sealed identities against what the evidence actually reports.

    Three distinct failures, kept distinct because they mean different things to whoever reads the
    verdict:

    * **missing from the seal** -- the run was authorized without binding this identity, so nothing
      was ever promised about it;
    * **missing from the evidence** -- the producers never reported it, so the promise cannot be
      checked;
    * **different** -- the promise was checked and broken.

    Collapsing these into "identity mismatch" would send someone to compare two digests when the
    real problem is that one of them was never recorded.
    """
    reasons: list[str] = []

    for kind in sorted(ALWAYS_REQUIRED):
        sealed_value = sealed.get(kind)
        observed_value = observed.get(kind)
        if sealed_value is None:
            reasons.append(
                f"{kind} is not bound by the sealed manifest, so nothing was promised about it "
                "and there is nothing to check the evidence against (INV-03)"
            )
            continue
        if observed_value is None:
            reasons.append(
                f"{kind} was sealed as {sealed_value[:12]}… but the evidence does not report it, "
                "so the binding cannot be confirmed"
            )
            continue
        if observed_value != sealed_value:
            reasons.append(
                f"{kind} changed: sealed {sealed_value[:12]}…, observed {observed_value[:12]}…"
            )

    # A model identity is optional but not ignorable. If the evidence reports one, it has to be the
    # one that was sealed: a run driven by a different model than the one approved is not the run
    # anyone approved, and silently accepting it is how a cheaper or newer model substitutes itself.
    sealed_model = sealed.get(IdentityKind.MODEL)
    observed_model = observed.get(IdentityKind.MODEL)
    if observed_model is not None and sealed_model != observed_model:
        reasons.append(
            f"the evidence reports model {observed_model} and the seal names "
            f"{sealed_model or 'none'}; a run driven by a different model is not the approved run"
        )
    if sealed_model is not None and observed_model is None:
        reasons.append(
            f"model {sealed_model} was sealed but the evidence does not report which model ran"
        )

    return IdentityVerdict(bound=not reasons, reasons=tuple(reasons))
