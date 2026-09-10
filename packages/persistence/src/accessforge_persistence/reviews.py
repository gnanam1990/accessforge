"""Human review, and the finding lifecycle it gates.

A review is one person's assessment of one repair, bound to the exact digests they looked at. Three
things this module refuses to let it become:

**A verdict about the run.** There is no path from here to `run.outcome`, and INV-12 requires human
opinion and machine outcome to stay separately attributable. A reviewer can dismiss a finding, ask
for changes, or say they could not tell; none of that rewrites what the evidence showed.

**An execution authority.** ACCEPT is not RUN_EFFECTS, not PATCH_APPLY, not GITHUB_PUBLISH and not a
deployment approval. Module 03's role matrix already separates `PATCH_REVIEW` from every execution
permission, and this module reads that matrix rather than restating it — a second copy would be a
second thing to keep in agreement.

**A claim about participation.** A review request and a submitted assessment are separate events.
Assignment establishes that someone was asked, which is not evidence that anyone looked.

There is also nowhere here to record demographic or disability information. Not an unused column,
not
a JSONB blob that could hold one. Normal product use requires no such disclosure, and a field that
exists gets filled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.authorization.roles import Permission, Role, role_permits
from accessforge_domain.states import FindingStatus, Outcome, ReviewVerdict
from accessforge_domain.timestamps import to_rfc3339_utc

#: Roles that may record a review verdict. Derived from the permission matrix rather than listed, so
#: a change to the matrix cannot leave this behind.
REVIEWER_ROLES: frozenset[Role] = frozenset(
    role for role in Role if role_permits(role, Permission.PATCH_REVIEW)
)


class ReviewError(Exception):
    """A review operation was refused."""


class StaleReviewContext(ReviewError):
    """The material moved since this review was prepared, so the assessment is about other bytes."""


class IndependencePolicyViolation(ReviewError):
    """The reviewer is not independent of the work being reviewed."""


@dataclass(frozen=True, slots=True)
class ReviewSubmission:
    """What a reviewer actually reported.

    ``limitations`` is required and may be empty. The distinction is deliberate: an empty string
    is a
    reviewer who considered what they could not check, and an absent field would be a reviewer who
    was never asked.
    """

    verdict: ReviewVerdict
    observations: str
    limitations: str
    used_assistive_technology: bool
    assistive_technology_detail: str | None = None

    def __post_init__(self) -> None:
        if not self.observations.strip():
            raise ReviewError(
                "a review must say what the reviewer observed. A verdict with no observations is "
                "an opinion with nothing behind it, and it is the reviewer's own name on it."
            )
        if self.verdict is ReviewVerdict.UNABLE_TO_ASSESS and not self.limitations.strip():
            raise ReviewError(
                "UNABLE_TO_ASSESS must say what was missing. Without that it records that a "
                "person's time was spent and nothing about why they could not answer."
            )
        if self.assistive_technology_detail and not self.used_assistive_technology:
            raise ReviewError(
                "assistive-technology detail was supplied but the reviewer did not report using "
                "any; one of the two is wrong and this record must not guess which"
            )


def _now(now: str | None) -> str:
    return now or to_rfc3339_utc(datetime.now(UTC))


def request_review(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    patch_digest: str,
    verification_digest: str,
    journey_version_id: str,
    environment_digest: str,
    requested_by: str,
    requested_of: str | None = None,
    now: str | None = None,
) -> str:
    """Record that a review was asked for. Establishes nothing about whether one happened."""
    request_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO review_request
            (id, workspace_id, patch_digest, verification_digest, journey_version_id,
             environment_digest, requested_by, requested_of, requested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            request_id,
            workspace_id,
            patch_digest,
            verification_digest,
            journey_version_id,
            environment_digest,
            requested_by,
            requested_of,
            _now(now),
        ),
    )
    return request_id


def submit_review(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    reviewer_id: str,
    reviewer_role: Role,
    patch_digest: str,
    verification_digest: str,
    journey_version_id: str,
    environment_digest: str,
    submission: ReviewSubmission,
    current_patch_digest: str,
    current_verification_digest: str,
    patch_author_id: str | None = None,
    require_independent_review: bool = True,
    request_id: str | None = None,
    supersedes: str | None = None,
    now: str | None = None,
) -> str:
    """Record one person's assessment of exact material.

    ``current_*`` are what the system holds *now*; the other digests are what the reviewer looked
    at.
    They are separate parameters because comparing a record to itself always agrees: passing the
    stored digest for both would make the staleness check unconditionally pass.

    Independence is enforced on ``reviewer_id``, the canonical actor identity. A display name, a
    second session or an alias resolves to the same value, which is the only reason the policy is
    worth having — enforced on anything a person controls, it would be a suggestion.
    """
    if reviewer_role not in REVIEWER_ROLES:
        raise ReviewError(
            f"{reviewer_role} does not hold {Permission.PATCH_REVIEW}. The roles that do are "
            f"{', '.join(sorted(REVIEWER_ROLES))}."
        )

    if patch_digest != current_patch_digest:
        raise StaleReviewContext(
            f"this review is of patch {patch_digest[:12]}… and the current patch is "
            f"{current_patch_digest[:12]}…. The bytes changed after the reviewer looked, so "
            "carrying their acceptance forward would attribute an opinion about other content to "
            "them. A new review context is required, not an amendment to this one."
        )
    if verification_digest != current_verification_digest:
        raise StaleReviewContext(
            "the verification result changed since this review was prepared; the reviewer assessed "
            "a repair whose proof no longer holds"
        )

    if require_independent_review and patch_author_id is not None:
        if reviewer_id == patch_author_id:
            raise IndependencePolicyViolation(
                "the reviewer authored this patch. Independent review means someone other than the "
                "author looked at it; a self-review satisfies the process and not the purpose."
            )

    review_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO review
            (id, workspace_id, request_id, reviewer_id, reviewer_role, patch_digest,
             verification_digest, journey_version_id, environment_digest, verdict, observations,
             limitations, used_assistive_technology, assistive_technology_detail, supersedes,
             submitted_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            review_id,
            workspace_id,
            request_id,
            reviewer_id,
            str(reviewer_role),
            patch_digest,
            verification_digest,
            journey_version_id,
            environment_digest,
            str(submission.verdict),
            submission.observations,
            submission.limitations,
            submission.used_assistive_technology,
            submission.assistive_technology_detail,
            supersedes,
            _now(now),
        ),
    )
    return review_id


def review_history(
    conn: psycopg.Connection[dict[str, Any]], *, patch_digest: str
) -> list[dict[str, Any]]:
    """Every review of this patch in the order they were submitted.

    Every one, including superseded ones. A corrected assessment does not erase the first: what a
    reviewer said and then said instead is the history, and showing only the latest would present a
    changed mind as a first impression.
    """
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, reviewer_id, reviewer_role, verdict, observations, limitations,
                   used_assistive_technology, assistive_technology_detail, supersedes, submitted_at
            FROM review WHERE patch_digest = %s ORDER BY submitted_at, id
            """,
            (patch_digest,),
        ).fetchall()
    ]


def effective_reviews(
    conn: psycopg.Connection[dict[str, Any]], *, patch_digest: str
) -> list[dict[str, Any]]:
    """Reviews that have not been superseded."""
    history = review_history(conn, patch_digest=patch_digest)
    superseded = {str(r["supersedes"]) for r in history if r["supersedes"] is not None}
    return [r for r in history if str(r["id"]) not in superseded]


# --- the finding lifecycle -----------------------------------------------------------------------


#: Which status transitions exist at all. RESOLVED has one route in, and it is gated separately.
_ADMISSIBLE: dict[FindingStatus, frozenset[FindingStatus]] = {
    FindingStatus.CANDIDATE: frozenset({FindingStatus.REPRODUCED, FindingStatus.DISMISSED}),
    FindingStatus.REPRODUCED: frozenset({FindingStatus.RESOLVED, FindingStatus.DISMISSED}),
    # A dismissed finding can be reopened, and reopening returns it to the evidence it actually has
    # rather than to a status someone chooses. A dismissal is a judgement about a finding, not a
    # deletion of it.
    FindingStatus.DISMISSED: frozenset({FindingStatus.CANDIDATE, FindingStatus.REPRODUCED}),
    FindingStatus.RESOLVED: frozenset({FindingStatus.REPRODUCED}),
}


def create_finding(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    run_id: str,
    assertion_id: str,
    summary: str,
    status: FindingStatus,
    run_outcome: Outcome,
    actor_id: str,
    now: str | None = None,
) -> str:
    """Open a finding at the status its evidence supports.

    REPRODUCED requires the run to have actually FAILed. A finding opened as REPRODUCED from an
    INCONCLUSIVE run would be a confirmed defect asserted on evidence that established nothing,
    which
    is the harm INV-02 exists to prevent — and it is easy to reach, because an inconclusive run
    frequently *contains* a real-looking failure.
    """
    if status is FindingStatus.REPRODUCED and run_outcome is not Outcome.FAIL:
        raise ReviewError(
            f"a finding cannot open as REPRODUCED from a run whose outcome is {run_outcome}. "
            "REPRODUCED means a complete valid run established the behaviour; an inconclusive run "
            "that contains a failure supports a CANDIDATE and nothing stronger (INV-02)."
        )
    if status in {FindingStatus.RESOLVED, FindingStatus.DISMISSED}:
        raise ReviewError(
            f"a finding does not open as {status}; it reaches that through a recorded transition "
            "with an actor and a reason"
        )

    moment = _now(now)
    finding_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO finding (id, workspace_id, run_id, assertion_id, status, summary, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (finding_id, workspace_id, run_id, assertion_id, str(status), summary, moment),
    )
    conn.execute(
        """
        INSERT INTO finding_transition
            (id, workspace_id, finding_id, from_status, to_status, actor_id, reason, occurred_at)
        VALUES (%s, %s, %s, NULL, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            workspace_id,
            finding_id,
            str(status),
            actor_id,
            "opened from run evidence",
            moment,
        ),
    )
    return finding_id


def transition_finding(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    finding_id: str,
    to_status: FindingStatus,
    actor_id: str,
    actor_role: Role,
    reason: str,
    expected_revision: int,
    review_id: str | None = None,
    now: str | None = None,
) -> None:
    """Move a finding, recording who and why.

    RESOLVED requires a review, and the review must be an ACCEPT. The module prompt is explicit that
    "RESOLVED requires a verified repair and required human review", and the check is here rather
    than in a caller because a caller that forgot it would produce a resolved finding
    indistinguishable
    from a properly reviewed one.

    A reviewer may dismiss or reopen. Neither touches the run: `run` is not written by this
    function,
    and a test asserts the outcome is unchanged afterwards.
    """
    if not role_permits(actor_role, Permission.PATCH_REVIEW):
        raise ReviewError(f"{actor_role} may not change a finding's status")
    if not reason.strip():
        raise ReviewError(
            "a status change needs a reason. The row exists so an auditor can ask who dismissed "
            "this and on what grounds; an empty reason answers half of that."
        )

    row = conn.execute(
        "SELECT status, revision FROM finding WHERE id = %s FOR UPDATE", (finding_id,)
    ).fetchone()
    if row is None:
        raise ReviewError("no such finding in this workspace")
    if int(row["revision"]) != expected_revision:
        raise ReviewError(
            f"finding is at revision {row['revision']}, caller expected {expected_revision}; the "
            "decision was made about a state that no longer exists"
        )

    current = FindingStatus(str(row["status"]))
    if to_status not in _ADMISSIBLE[current]:
        allowed = ", ".join(sorted(_ADMISSIBLE[current])) or "nothing"
        raise ReviewError(f"a {current} finding cannot become {to_status}; admissible: {allowed}")

    if to_status is FindingStatus.RESOLVED:
        if review_id is None:
            raise ReviewError(
                "RESOLVED requires the human review that accepted the repair. A finding marked "
                "resolved with no review attached is indistinguishable from one nobody looked at."
            )
        verdict = conn.execute("SELECT verdict FROM review WHERE id = %s", (review_id,)).fetchone()
        if verdict is None:
            raise ReviewError("the cited review does not exist in this workspace")
        if str(verdict["verdict"]) != ReviewVerdict.ACCEPT:
            raise ReviewError(
                f"the cited review is {verdict['verdict']}, not ACCEPT. A review that asked for "
                "changes, or one that could not assess the repair, does not resolve anything."
            )

    moment = _now(now)
    conn.execute(
        "UPDATE finding SET status = %s, revision = revision + 1 WHERE id = %s",
        (str(to_status), finding_id),
    )
    conn.execute(
        """
        INSERT INTO finding_transition
            (id, workspace_id, finding_id, from_status, to_status, actor_id, reason, review_id,
             occurred_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            workspace_id,
            finding_id,
            str(current),
            str(to_status),
            actor_id,
            reason,
            review_id,
            moment,
        ),
    )


def finding_history(
    conn: psycopg.Connection[dict[str, Any]], *, finding_id: str
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT from_status, to_status, actor_id, reason, review_id, occurred_at
            FROM finding_transition WHERE finding_id = %s ORDER BY occurred_at, id
            """,
            (finding_id,),
        ).fetchall()
    ]


def serialize_for_display(
    conn: psycopg.Connection[dict[str, Any]], *, finding_id: str
) -> dict[str, Any]:
    """A view that keeps human opinion and machine outcome separately attributable (INV-12).

    Two top-level keys, never merged into one "status" a reader could take as a single authority.
    The machine outcome is what the evidence established; the human assessments are what people said
    about a repair. A view that flattened them would let a reviewer's ACCEPT read as the system
    having verified something.
    """
    finding = conn.execute(
        """
        SELECT f.id, f.status, f.assertion_id, f.summary, f.run_id, r.outcome AS run_outcome,
               r.status AS run_status
        FROM finding f JOIN run r ON r.id = f.run_id
        WHERE f.id = %s
        """,
        (finding_id,),
    ).fetchone()
    if finding is None:
        raise ReviewError("no such finding in this workspace")

    reviews = conn.execute(
        """
        SELECT rv.id, rv.reviewer_id, rv.verdict, rv.observations, rv.limitations,
               rv.used_assistive_technology, rv.submitted_at
        FROM finding_transition ft JOIN review rv ON rv.id = ft.review_id
        WHERE ft.finding_id = %s ORDER BY rv.submitted_at
        """,
        (finding_id,),
    ).fetchall()

    return {
        "machineOutcome": {
            "runId": str(finding["run_id"]),
            "runStatus": str(finding["run_status"]),
            "runOutcome": str(finding["run_outcome"]),
            "assertionId": str(finding["assertion_id"]),
            "establishedBy": "deterministic evaluation of recorded evidence",
        },
        "humanAssessments": [
            {
                "reviewId": str(r["id"]),
                "reviewerId": str(r["reviewer_id"]),
                "verdict": str(r["verdict"]),
                "observations": str(r["observations"]),
                "limitations": str(r["limitations"]),
                "usedAssistiveTechnology": bool(r["used_assistive_technology"]),
                "submittedAt": str(r["submitted_at"]),
                "establishedBy": "a person's judgement, attributable to them",
            }
            for r in reviews
        ],
        "findingStatus": str(finding["status"]),
        "summary": str(finding["summary"]),
    }
