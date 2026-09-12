"""Proposing a repair, authorizing it exactly, and refusing to call it verified.

FR-010 and FR-011. The shape of this module is the argument: proposing is cheap, approving is exact,
and concluding VERIFIED is hard on purpose.

**What makes a patch proposable.** It names a finding, a base identity in both halves (the sealed
manifest the baseline ran against *and* the exact source tree), and a set of changes the policy in
`accessforge_domain.patch_policy` allows. A patch touching a protected path is refused outright, not
recorded as rejected: there is no version of "we considered editing the test" worth storing.

**What makes an approval usable.** `PATCH_APPLY`, bound to this patch's digest and this patch's
revision, unexpired and unrevoked, rechecked at the moment of dispatch rather than at approval time.
A changed base or changed patch bytes make it stale -- and stale here means the approval is gone,
not weakened.

**What makes a repair VERIFIED.** A complete valid failed baseline, a candidate run that actually
ran, identities that differ only by the approved patch and explicitly recorded permitted
differences, closing watermarks from every required producer, and passing protected regressions.
Anything unknown is INCONCLUSIVE. There is no argument, no quorum and no human override that reaches
VERIFIED: CONTRACTS says "human review cannot convert an INCONCLUSIVE candidate to VERIFIED", and
the
way to honour that is to have no parameter that could.

**What this module cannot do, today.** It cannot produce a candidate. Building one requires
executing
the target application's build inside a containment boundary this deployment has not got, and
running
it requires a real screen reader on a real desktop. So `conclude` is reachable with NOT_ESTABLISHED
and INCONCLUSIVE, and VERIFIED is reachable only by supplying evidence that, here, nothing can
produce. The gates are tested; the happy path is not claimed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.authority import AuthorityError
from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import (
    PatchInspection,
    ProposedChange,
    inspect_patch,
)
from accessforge_domain.states import ApprovalScope, PatchStatus
from accessforge_domain.timestamps import to_rfc3339_utc

from . import approvals


class PatchError(Exception):
    """A patch could not be proposed, approved or moved."""


class PatchRefused(PatchError):
    """The policy refused the patch. Not a status -- the proposal does not exist."""

    def __init__(self, message: str, inspection: PatchInspection) -> None:
        super().__init__(message)
        self.inspection = inspection


class StalePatchBase(PatchError):
    """The base identity or the patch bytes moved after approval."""


class VerificationError(Exception):
    """A verification could not be opened, advanced or concluded."""


#: Transitions the proposal lifecycle permits. Written out rather than derived, so that reading this
#: dict is reading the contract: `PROPOSED → APPROVED → BUILDING → VERIFYING → VERIFIED →
#: REVIEW_ACCEPTED`, with REJECTED, STALE and FAILED as terminal alternatives.
#:
#: Note what is absent. Nothing reaches VERIFIED except VERIFYING, and nothing leaves VERIFIED
#: except
#: REVIEW_ACCEPTED -- a verified repair cannot be quietly re-approved, and a failed one cannot be
#: nudged forward without going back through approval against the current base.
ALLOWED_TRANSITIONS: dict[PatchStatus, frozenset[PatchStatus]] = {
    PatchStatus.PROPOSED: frozenset(
        {PatchStatus.APPROVED, PatchStatus.REJECTED, PatchStatus.STALE}
    ),
    PatchStatus.APPROVED: frozenset(
        {PatchStatus.BUILDING, PatchStatus.REJECTED, PatchStatus.STALE}
    ),
    PatchStatus.BUILDING: frozenset({PatchStatus.VERIFYING, PatchStatus.FAILED, PatchStatus.STALE}),
    PatchStatus.VERIFYING: frozenset({PatchStatus.VERIFIED, PatchStatus.FAILED, PatchStatus.STALE}),
    PatchStatus.VERIFIED: frozenset({PatchStatus.REVIEW_ACCEPTED}),
    PatchStatus.REVIEW_ACCEPTED: frozenset(),
    PatchStatus.REJECTED: frozenset(),
    PatchStatus.STALE: frozenset(),
    PatchStatus.FAILED: frozenset(),
}


def patch_digest(changes: tuple[ProposedChange, ...]) -> str:
    """The digest an approval binds to.

    Over path, content and mode together. Over content alone, two patches writing the same bytes to
    different files would share a digest, so an approval for one would authorize the other -- and
    the
    other could be anywhere the policy allows. Over paths alone, the bytes could change entirely
    under a live approval, which is the whole failure `target_digest` exists to prevent.
    """
    return digest(
        [
            {"path": c.path, "content": c.content, "mode": c.mode, "binary": c.binary}
            for c in sorted(changes, key=lambda c: c.path)
        ]
    )


@dataclass(frozen=True, slots=True)
class PatchProposal:
    """A recorded proposal, and nothing about whether it works."""

    patch_id: str
    finding_id: str
    base_manifest_digest: str
    base_source_digest: str
    patch_digest: str
    changed_paths: tuple[str, ...]
    separately_reviewed_paths: tuple[str, ...]
    status: PatchStatus
    approval_id: str | None
    proposed_by: str
    rationale: str
    revision: int
    created_at: str

    @property
    def meaning(self) -> str:
        return (
            "A proposed change to application source. Recording it establishes nothing about "
            "whether it repairs the finding; that requires a matched verification, and the "
            f"current status is {self.status}."
        )


def propose_patch(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    finding_id: str,
    base_manifest_digest: str,
    base_source_digest: str,
    changes: tuple[ProposedChange, ...],
    rationale: str,
    proposed_by: str,
    application_paths: tuple[str, ...] = (),
    acknowledge_separate_review: bool = False,
    now: datetime | None = None,
) -> PatchProposal:
    """Record a patch the policy allows, against a base that exists.

    Four refusals before anything is stored, in this order:

    1. **An empty patch.** A proposal changing nothing would sit in the listing looking like work.
    2. **A base nobody sealed.** The manifest digest must name a sealed manifest in this workspace.
       Without this the base is taken on the caller's word, and an approval would bind to an
       identity
       matching nothing.
    3. **The policy.** Protected paths, traversal, symlinks, binaries: refused, not recorded.
    4. **Unacknowledged dependency scope.** Lockfile and manifest changes are allowed but not as
       routine accessibility edits, so the caller has to say they know.
    """
    moment = now or datetime.now(UTC)
    if not changes:
        raise PatchError(
            "a patch must change something. An empty proposal would appear in the finding's "
            "listing as a repair somebody offered."
        )
    if not rationale.strip():
        raise PatchError(
            "state why this change repairs the finding. A patch nobody can explain is the one a "
            "reviewer has to reverse-engineer from the diff."
        )

    sealed = conn.execute(
        "SELECT 1 FROM sealed_manifest WHERE manifest_digest = %s LIMIT 1",
        (base_manifest_digest,),
    ).fetchone()
    if sealed is None:
        raise PatchError(
            "no sealed manifest in this workspace has that digest, so there is no base identity to "
            "patch against. A proposal against an unsealed base could never be approved: the "
            "approval would bind to inputs nothing recorded."
        )

    inspection = inspect_patch(changes, application_paths=application_paths)
    if not inspection.acceptable:
        raise PatchRefused(
            "this patch touches paths no repair may change:\n" + inspection.explain(), inspection
        )
    if inspection.separately_reviewed and not acknowledge_separate_review:
        raise PatchRefused(
            "this patch changes dependency or build description, which is reviewed as its own "
            "scope rather than as an accessibility edit:\n" + inspection.explain(),
            inspection,
        )

    patch_id = str(uuid.uuid4())
    paths = tuple(r.path for r in inspection.rulings)
    separate = tuple(r.path for r in inspection.separately_reviewed)
    conn.execute(
        """
        INSERT INTO patch_proposal
            (id, workspace_id, finding_id, base_manifest_digest, base_source_digest,
             patch_digest, changed_paths, separately_reviewed_paths, status,
             proposed_by, rationale, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PROPOSED', %s, %s, %s)
        """,
        (
            patch_id,
            workspace_id,
            finding_id,
            base_manifest_digest,
            base_source_digest,
            patch_digest(changes),
            list(paths),
            list(separate),
            proposed_by,
            rationale,
            moment,
        ),
    )
    _record_transition(
        conn,
        workspace_id=workspace_id,
        patch_id=patch_id,
        from_status=None,
        to_status=PatchStatus.PROPOSED,
        actor_id=proposed_by,
        reason=rationale,
        moment=moment,
    )
    return load_patch(conn, patch_id=patch_id)


def load_patch(conn: psycopg.Connection[dict[str, Any]], *, patch_id: str) -> PatchProposal:
    row = conn.execute(
        """
        SELECT id, finding_id, base_manifest_digest, base_source_digest, patch_digest,
               changed_paths, separately_reviewed_paths, status, approval_id, proposed_by,
               rationale, revision, created_at
          FROM patch_proposal WHERE id = %s
        """,
        (patch_id,),
    ).fetchone()
    if row is None:
        raise PatchError(f"no patch proposal {patch_id} is visible here")
    return PatchProposal(
        patch_id=str(row["id"]),
        finding_id=str(row["finding_id"]),
        base_manifest_digest=str(row["base_manifest_digest"]),
        base_source_digest=str(row["base_source_digest"]),
        patch_digest=str(row["patch_digest"]),
        changed_paths=tuple(row["changed_paths"]),
        separately_reviewed_paths=tuple(row["separately_reviewed_paths"]),
        status=PatchStatus(str(row["status"])),
        approval_id=None if row["approval_id"] is None else str(row["approval_id"]),
        proposed_by=str(row["proposed_by"]),
        rationale=str(row["rationale"]),
        revision=int(row["revision"]),
        created_at=to_rfc3339_utc(row["created_at"]),
    )


def patches_for_finding(
    conn: psycopg.Connection[dict[str, Any]], *, finding_id: str
) -> list[PatchProposal]:
    """Every patch proposed for a finding, oldest first.

    All of them, including rejected and stale ones. A finding that took four attempts is a different
    story from one that took one, and hiding the failures would tell the second story.
    """
    rows = conn.execute(
        "SELECT id FROM patch_proposal WHERE finding_id = %s ORDER BY created_at, id",
        (finding_id,),
    ).fetchall()
    return [load_patch(conn, patch_id=str(row["id"])) for row in rows]


def _record_transition(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    patch_id: str,
    from_status: PatchStatus | None,
    to_status: PatchStatus,
    actor_id: str | None,
    reason: str,
    moment: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO patch_proposal_transition
            (workspace_id, patch_id, from_status, to_status, actor_id, reason, occurred_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            workspace_id,
            patch_id,
            None if from_status is None else str(from_status),
            str(to_status),
            actor_id,
            reason,
            moment,
        ),
    )


def transition_patch(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    patch_id: str,
    to_status: PatchStatus,
    actor_id: str | None,
    reason: str,
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> PatchProposal:
    """Move a patch, refusing any move the lifecycle does not permit.

    `expected_revision` is the optimistic lock. Two reviewers acting on what they each read as a
    PROPOSED patch must not both succeed: the second is told the patch moved, rather than silently
    overwriting a decision made a moment earlier.
    """
    moment = now or datetime.now(UTC)
    current = load_patch(conn, patch_id=patch_id)
    if expected_revision is not None and current.revision != expected_revision:
        raise PatchError(
            f"patch {patch_id} is at revision {current.revision}, not {expected_revision}. "
            "Somebody else acted on it since you read it."
        )
    permitted = ALLOWED_TRANSITIONS[current.status]
    if to_status not in permitted:
        allowed = ", ".join(sorted(str(s) for s in permitted)) or "nothing; it is terminal"
        raise PatchError(
            f"a patch at {current.status} cannot move to {to_status}. From here: {allowed}."
        )
    if not reason.strip():
        raise PatchError("state a reason for the transition; the history is read by reviewers")

    conn.execute(
        "UPDATE patch_proposal SET status = %s, revision = revision + 1 WHERE id = %s",
        (str(to_status), patch_id),
    )
    _record_transition(
        conn,
        workspace_id=workspace_id,
        patch_id=patch_id,
        from_status=current.status,
        to_status=to_status,
        actor_id=actor_id,
        reason=reason,
        moment=moment,
    )
    return load_patch(conn, patch_id=patch_id)


def approve_patch(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    patch_id: str,
    approval_id: str,
    actor_id: str,
    now: datetime | None = None,
) -> PatchProposal:
    """Attach a PATCH_APPLY approval, checking that it authorizes exactly this patch.

    The check is the domain's, not this module's. Every field is compared: a `RUN_EFFECTS` approval
    is not a weaker `PATCH_APPLY`, an approval for another patch is not an approval for this one,
    and
    an approval whose `target_digest` no longer matches the patch bytes is not an approval at all.
    """
    moment = now or datetime.now(UTC)
    patch = load_patch(conn, patch_id=patch_id)
    approval = approvals.load_for_check(conn, approval_id=approval_id)
    try:
        approval.check(
            now=to_rfc3339_utc(moment),
            scope=ApprovalScope.PATCH_APPLY,
            workspace_id=workspace_id,
            target_id=patch_id,
            target_digest=patch.patch_digest,
            current_revision=patch.revision,
        )
    except AuthorityError as exc:
        raise PatchError(f"this approval does not authorize applying this patch: {exc}") from exc

    updated = transition_patch(
        conn,
        workspace_id=workspace_id,
        patch_id=patch_id,
        to_status=PatchStatus.APPROVED,
        actor_id=actor_id,
        reason=f"approved under {approval_id}",
        expected_revision=patch.revision,
        now=moment,
    )
    conn.execute(
        "UPDATE patch_proposal SET approval_id = %s WHERE id = %s", (approval_id, patch_id)
    )
    return load_patch(conn, patch_id=updated.patch_id)


def assert_dispatchable(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    patch_id: str,
    current_source_digest: str,
    now: datetime | None = None,
) -> PatchProposal:
    """Recheck, at dispatch, that this patch may still be applied.

    Separate from `approve_patch` because the gap between the two is where the interesting failures
    live. Between approving and applying: the approval can be revoked, it can expire, the source
    tree
    can be rebuilt under it. Each of those ends the dispatch, and the caller is told which.

    A moved source digest raises `StalePatchBase` rather than a generic error, because the caller's
    correct response differs: a revoked approval means ask again, and a moved base means propose
    again against what is there now.
    """
    moment = now or datetime.now(UTC)
    patch = load_patch(conn, patch_id=patch_id)
    if patch.status is not PatchStatus.APPROVED:
        raise PatchError(
            f"patch {patch_id} is {patch.status}, and only an APPROVED patch may be applied"
        )
    if patch.approval_id is None:
        raise PatchError(
            f"patch {patch_id} is APPROVED with no recorded approval, which should be impossible; "
            "refusing rather than applying it"
        )
    if current_source_digest != patch.base_source_digest:
        raise StalePatchBase(
            "the source tree has changed since this patch was approved "
            f"({patch.base_source_digest} -> {current_source_digest}). The approval bound to the "
            "old base and does not carry over: a diff applied to a tree it was not written against "
            "is a different change."
        )

    approval = approvals.load_for_check(conn, approval_id=patch.approval_id)
    try:
        approval.check(
            now=to_rfc3339_utc(moment),
            scope=ApprovalScope.PATCH_APPLY,
            workspace_id=workspace_id,
            target_id=patch_id,
            target_digest=patch.patch_digest,
            current_revision=approval.expected_revision,
        )
    except AuthorityError as exc:
        raise PatchError(f"the approval no longer authorizes this application: {exc}") from exc
    return patch


# --- FR-011: verification -------------------------------------------------------------------------


#: Producers whose closing watermark a matched pair requires. Absent any one of them the candidate
#: evidence has a tail nobody can account for, and a contiguous chain does not cover it (INV-06).
REQUIRED_PRODUCER_ROLES: tuple[str, ...] = ("supervisor", "observer")


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """What a caller claims about a candidate run, for the gates to disbelieve.

    Every field defaults to the unproven value. A caller that forgets to set one gets INCONCLUSIVE
    rather than a pass, which is the direction a default has to fail in.
    """

    candidate_run_id: str | None = None
    candidate_identity: dict[str, Any] = field(default_factory=dict)
    baseline_outcome: str | None = None
    candidate_outcome: str | None = None
    closing_watermarks: tuple[str, ...] = ()
    protected_regressions_passed: bool = False
    frozen_assertions_unchanged: bool = False
    permitted_differences: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    """One attempt to establish a repair, and what it concluded."""

    verification_id: str
    patch_id: str
    baseline_run_id: str
    candidate_run_id: str | None
    state: str
    conclusion: str | None
    reasons: tuple[str, ...]
    revision: int
    created_at: str

    @property
    def meaning(self) -> str:
        if self.conclusion is None:
            return (
                "This verification is still open. Nothing here says the repair works, and the "
                "finding it is about remains exactly as it was."
            )
        if self.conclusion == "VERIFIED":
            return (
                "A matched pair established that the approved patch repaired the reproduced "
                "behaviour. It does not mean the application is accessible, and it is not a "
                "compliance statement: it means this assertion, on this journey, with this reader "
                "profile, went from failing to passing with no other recorded difference."
            )
        if self.conclusion == "NOT_ESTABLISHED":
            return (
                "The repair was not established. The original finding and all of its evidence "
                "survive this unchanged -- a failed verification removes nothing."
            )
        return (
            "The comparison could not establish anything either way, so nothing about the repair "
            "is claimed. `reasons` says what was missing; an inconclusive candidate can never "
            "become verified by review."
        )


def open_verification(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    patch_id: str,
    baseline_run_id: str,
    baseline_identity: dict[str, Any],
    now: datetime | None = None,
) -> VerificationRecord:
    """Begin a verification for an approved patch, moving it to BUILDING.

    Requires the patch to be APPROVED: a verification of an unapproved patch would be a candidate
    built without authority, and the record of it would imply there had been some.
    """
    moment = now or datetime.now(UTC)
    patch = load_patch(conn, patch_id=patch_id)
    if patch.status is not PatchStatus.APPROVED:
        raise VerificationError(
            f"patch {patch_id} is {patch.status}. A verification begins from APPROVED -- "
            "building a candidate from anything else would be work nobody authorized."
        )

    verification_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO patch_verification
            (id, workspace_id, patch_id, baseline_run_id, baseline_identity, state, created_at)
        VALUES (%s, %s, %s, %s, %s::jsonb, 'BUILDING', %s)
        """,
        (
            verification_id,
            workspace_id,
            patch_id,
            baseline_run_id,
            json.dumps(baseline_identity),
            moment,
        ),
    )
    transition_patch(
        conn,
        workspace_id=workspace_id,
        patch_id=patch_id,
        to_status=PatchStatus.BUILDING,
        actor_id=None,
        reason=f"verification {verification_id} opened",
        now=moment,
    )
    return load_verification(conn, verification_id=verification_id)


def load_verification(
    conn: psycopg.Connection[dict[str, Any]], *, verification_id: str
) -> VerificationRecord:
    row = conn.execute(
        """
        SELECT id, patch_id, baseline_run_id, candidate_run_id, state, conclusion,
               conclusion_reasons, revision, created_at
          FROM patch_verification WHERE id = %s
        """,
        (verification_id,),
    ).fetchone()
    if row is None:
        raise VerificationError(f"no verification {verification_id} is visible here")
    return VerificationRecord(
        verification_id=str(row["id"]),
        patch_id=str(row["patch_id"]),
        baseline_run_id=str(row["baseline_run_id"]),
        candidate_run_id=None if row["candidate_run_id"] is None else str(row["candidate_run_id"]),
        state=str(row["state"]),
        conclusion=None if row["conclusion"] is None else str(row["conclusion"]),
        reasons=tuple(row["conclusion_reasons"]),
        revision=int(row["revision"]),
        created_at=to_rfc3339_utc(row["created_at"]),
    )


def _unmet_gates(
    patch: PatchProposal, evidence: CandidateEvidence, baseline_identity: dict[str, Any]
) -> list[str]:
    """Every reason this pair does not establish the repair.

    Returned as a list rather than a boolean because the list *is* the product. "Not verified" tells
    a reader nothing they can act on; "the candidate ran against a different reader profile" tells
    them what to fix. Collected in full rather than short-circuiting, for the same reason.
    """
    unmet: list[str] = []

    if evidence.candidate_run_id is None:
        unmet.append(
            "no candidate run: there is nothing to compare the baseline against. Until a candidate "
            "has actually run, the only honest statement is that the repair is untested."
        )
    if evidence.baseline_outcome != "FAIL":
        unmet.append(
            f"the baseline outcome is {evidence.baseline_outcome or 'unrecorded'}, not FAIL. A "
            "repair can only be established against a failure that was established first; "
            "otherwise a passing candidate proves the behaviour was never broken (INV-02)."
        )
    if evidence.candidate_outcome != "PASS":
        unmet.append(
            f"the candidate outcome is {evidence.candidate_outcome or 'unrecorded'}, not PASS. "
            "Anything else -- including INCONCLUSIVE -- leaves the repair unestablished."
        )

    missing = [p for p in REQUIRED_PRODUCER_ROLES if p not in evidence.closing_watermarks]
    if missing:
        unmet.append(
            f"missing closing watermarks from: {', '.join(missing)}. A producer that stopped "
            "without closing leaves a tail of the candidate run nobody can account for, and a "
            "contiguous chain does not cover that (INV-06)."
        )
    if not evidence.protected_regressions_passed:
        unmet.append(
            "protected functional regressions did not pass, or were not run. Validation, "
            "authorization, successful submission and failed-input handling must still work: a "
            "patch that fixes an announcement by removing the thing announced passes the "
            "accessibility assertion and breaks the application."
        )
    if not evidence.frozen_assertions_unchanged:
        unmet.append(
            "the frozen assertions or the observer contract changed between the baseline and the "
            "candidate. Then the candidate answered an easier question, and comparing the two "
            "answers establishes nothing (INV-03)."
        )

    permitted = {
        (str(d.get("field")), str(d.get("candidate"))) for d in evidence.permitted_differences
    }
    drift = sorted(
        key
        for key in set(baseline_identity) | set(evidence.candidate_identity)
        if baseline_identity.get(key) != evidence.candidate_identity.get(key)
        and (key, str(evidence.candidate_identity.get(key))) not in permitted
    )
    if drift:
        unmet.append(
            f"unexplained identity drift in: {', '.join(drift)}. INV-04 allows the baseline and "
            "candidate to differ by the approved patch and by differences explicitly recorded as "
            "permitted -- nothing else. An unrecorded difference means the two runs are not a pair."
        )
    if patch.status is not PatchStatus.VERIFYING:
        unmet.append(
            f"the patch is {patch.status}, not VERIFYING, so this conclusion would not correspond "
            "to a candidate this product built under the recorded approval."
        )
    return unmet


def conclude_verification(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    verification_id: str,
    evidence: CandidateEvidence,
    now: datetime | None = None,
) -> VerificationRecord:
    """Derive the conclusion from the evidence. There is no parameter for the answer.

    Deliberately: a `conclusion=` argument is all it would take for a route, a retry or a reviewer
    to
    write VERIFIED over an inconclusive candidate, and CONTRACTS forbids exactly that. The caller
    supplies what it observed; the gates decide.

    `candidate_outcome` being absent is INCONCLUSIVE, not NOT_ESTABLISHED. A run that did not
    produce
    a verdict has not shown the repair failed -- it has shown nothing -- and recording those as the
    same thing would let an infrastructure problem read as a rejected repair.
    """
    moment = now or datetime.now(UTC)
    record = load_verification(conn, verification_id=verification_id)
    if record.state == "CONCLUDED":
        raise VerificationError(
            f"verification {verification_id} already concluded as {record.conclusion}. A frozen "
            "outcome is not rewritten; run another verification instead."
        )

    row = conn.execute(
        "SELECT baseline_identity FROM patch_verification WHERE id = %s", (verification_id,)
    ).fetchone()
    baseline_identity: dict[str, Any] = dict(row["baseline_identity"]) if row else {}

    # Record the candidate and move to VERIFYING first, so that the patch state the gates read is
    # the state a real verification would be in at this point.
    if evidence.candidate_run_id is not None:
        conn.execute(
            "UPDATE patch_verification SET candidate_run_id = %s, candidate_identity = %s::jsonb, "
            "    permitted_differences = %s::jsonb, state = 'VERIFYING' WHERE id = %s",
            (
                evidence.candidate_run_id,
                json.dumps(evidence.candidate_identity),
                json.dumps(list(evidence.permitted_differences)),
                verification_id,
            ),
        )
        patch_now = load_patch(conn, patch_id=record.patch_id)
        if patch_now.status is PatchStatus.BUILDING:
            transition_patch(
                conn,
                workspace_id=workspace_id,
                patch_id=record.patch_id,
                to_status=PatchStatus.VERIFYING,
                actor_id=None,
                reason=f"candidate {evidence.candidate_run_id} recorded",
                now=moment,
            )

    patch = load_patch(conn, patch_id=record.patch_id)
    unmet = _unmet_gates(patch, evidence, baseline_identity)

    if not unmet:
        conclusion = "VERIFIED"
        reasons = [
            "a complete matched pair: the baseline failed, the candidate passed, every required "
            "producer closed, protected regressions held and the frozen assertions were unchanged",
            "identities differed only by the approved patch and the recorded permitted differences",
        ]
    elif evidence.candidate_outcome in {"PASS", "FAIL"} and evidence.candidate_run_id is not None:
        conclusion = "NOT_ESTABLISHED"
        reasons = unmet
    else:
        conclusion = "INCONCLUSIVE"
        reasons = unmet

    conn.execute(
        "UPDATE patch_verification SET state = 'CONCLUDED', conclusion = %s, "
        "    conclusion_reasons = %s, concluded_at = %s, revision = revision + 1 WHERE id = %s",
        (conclusion, reasons, moment, verification_id),
    )

    target = PatchStatus.VERIFIED if conclusion == "VERIFIED" else PatchStatus.FAILED
    if target in ALLOWED_TRANSITIONS[patch.status]:
        transition_patch(
            conn,
            workspace_id=workspace_id,
            patch_id=record.patch_id,
            to_status=target,
            actor_id=None,
            reason=f"verification {verification_id} concluded {conclusion}",
            now=moment,
        )
    return load_verification(conn, verification_id=verification_id)


def verifications_for_patch(
    conn: psycopg.Connection[dict[str, Any]], *, patch_id: str
) -> list[VerificationRecord]:
    """Every verification attempted for a patch, oldest first.

    All attempts, never a selected one. Freezing the repetition policy and reporting everything is
    the difference between a verification and a search for a favourable result.
    """
    rows = conn.execute(
        "SELECT id FROM patch_verification WHERE patch_id = %s ORDER BY created_at, id",
        (patch_id,),
    ).fetchall()
    return [load_verification(conn, verification_id=str(r["id"])) for r in rows]
