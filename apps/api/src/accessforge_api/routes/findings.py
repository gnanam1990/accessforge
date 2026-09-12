"""Findings and human reviews.

The route-level job here is to keep the two apart. A finding's status and a reviewer's verdict are
different kinds of claim, and the serialised form says which is which — `machineOutcome` against
`humanAssessments`, each carrying how it was established (INV-12). A flattened response would let a
reviewer's ACCEPT read as the system having verified something.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size, require_if_match
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission, Role
from accessforge_domain.states import FindingStatus, ReviewVerdict
from accessforge_persistence import reviews

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["findings"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


@router.get("/findings")
def list_findings(
    workspace_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    rows = conn.execute(
        """
        SELECT id, run_id, assertion_id, status, summary, revision, created_at
        FROM finding WHERE (%s::uuid IS NULL OR id > %s::uuid) ORDER BY id LIMIT %s
        """,
        (after, after, size + 1),
    ).fetchall()
    items = [
        {
            "findingId": str(r["id"]),
            "runId": str(r["run_id"]),
            "assertionId": str(r["assertion_id"]),
            "status": str(r["status"]),
            "summary": str(r["summary"]),
            "revision": int(r["revision"]),
            "createdAt": str(r["created_at"]),
        }
        for r in rows[:size]
    ]
    return {"items": items, "nextCursor": items[-1]["findingId"] if len(rows) > size else None}


@router.get("/findings/{finding_id}")
def get_finding(
    workspace_id: str, finding_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    try:
        view = reviews.serialize_for_display(conn, finding_id=finding_id)
    except reviews.ReviewError as exc:
        raise not_found() from exc

    revision = conn.execute("SELECT revision FROM finding WHERE id = %s", (finding_id,)).fetchone()
    if revision is not None:
        response.headers["ETag"] = f'"{int(revision["revision"])}"'
    view["history"] = [
        {
            "fromStatus": h["from_status"],
            "toStatus": str(h["to_status"]),
            "actorId": str(h["actor_id"]),
            "reason": str(h["reason"]),
            "reviewId": None if h["review_id"] is None else str(h["review_id"]),
            "occurredAt": str(h["occurred_at"]),
        }
        for h in reviews.finding_history(conn, finding_id=finding_id)
    ]
    return view


@router.post("/findings/{finding_id}/transitions", status_code=status.HTTP_200_OK)
def transition_finding(
    workspace_id: str, finding_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Move a finding, recording who and why.

    RESOLVED requires an accepting review, enforced by the domain rather than here. The route passes
    the reviewer's role straight through: a route that decided for itself which roles may transition
    a finding would be a second copy of the permission matrix.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PATCH_REVIEW,
        body,
        frozenset({"toStatus", "reason", "reviewId"}),
    )
    expected = require_if_match(context)

    try:
        target = FindingStatus(str(body["toStatus"]))
    except (KeyError, ValueError) as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"toStatus must be one of {', '.join(sorted(FindingStatus))}",
            request_id=context.request_id,
        ) from exc

    try:
        reviews.transition_finding(
            conn,
            workspace_id=workspace_id,
            finding_id=finding_id,
            to_status=target,
            actor_id=context.principal.user_id,
            actor_role=context.principal.role,
            reason=str(body.get("reason", "")),
            expected_revision=expected,
            review_id=body.get("reviewId"),
        )
    except reviews.ReviewError as exc:
        message = str(exc)
        if "no such finding" in message:
            raise not_found() from exc
        code = ProblemCode.STALE_REVISION if "no longer exists" in message else ProblemCode.CONFLICT
        raise ProblemDetail(code, message, request_id=context.request_id) from exc

    return {"findingId": finding_id, "status": str(target)}


@router.get("/review-requests")
def list_review_requests(
    workspace_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Reviews that have been asked for, and whether anyone has answered.

    A request and an assessment are different events, and this listing keeps them that way: an
    entry with `reviewCount: 0` records that somebody was asked, and nothing more. Treating an
    assignment as a review is how a process reports completed review that never happened.

    `currentPatchDigest` and `currentVerificationDigest` are what the request was bound to. They are
    reported under those names because a review form has to send both what the reviewer looked at
    and what the system holds now, and a form that read one value and sent it twice would make the
    staleness check agree with itself.

    **There is no route that creates one.** A request binds a patch digest and a verification
    digest, and nothing in this system produces either: modules 14 and 15 do not exist. A create
    route would have to accept digests invented by its caller.
    """
    authorize(conn, request, workspace_id, Permission.PATCH_REVIEW)
    if after is not None:
        as_identifier(after, what="the page cursor")
    size = clamp_page_size(limit)
    rows = conn.execute(
        """
        SELECT rq.id, rq.patch_digest, rq.verification_digest, rq.journey_version_id,
               rq.environment_digest, rq.requested_by, rq.requested_of, rq.requested_at,
               (SELECT count(*) FROM review rv WHERE rv.request_id = rq.id) AS review_count
        FROM review_request rq
        WHERE (%s::uuid IS NULL OR rq.id > %s::uuid)
        ORDER BY rq.id
        LIMIT %s
        """,
        (after, after, size + 1),
    ).fetchall()
    items = [
        {
            "reviewRequestId": str(r["id"]),
            "patchDigest": str(r["patch_digest"]),
            "verificationDigest": str(r["verification_digest"]),
            "journeyVersionId": str(r["journey_version_id"]),
            "environmentDigest": str(r["environment_digest"]),
            "requestedBy": str(r["requested_by"]),
            "requestedOf": None if r["requested_of"] is None else str(r["requested_of"]),
            "requestedAt": str(r["requested_at"]),
            "reviewCount": int(r["review_count"]),
        }
        for r in rows[:size]
    ]
    return {
        "items": items,
        # Keyset, like every other listing here. An earlier version took the first page with no
        # cursor and no indication that it had stopped -- the same shape a review found in module
        # 22's environment listing, written again from memory a day later.
        "nextCursor": items[-1]["reviewRequestId"] if len(rows) > size else None,
        "meaning": (
            "Asking for a review is an event; an assessment is a different event. An entry with no "
            "reviews records that somebody was asked and nothing about whether they looked."
        ),
    }


@router.get("/reviews/{review_id}")
def get_review(workspace_id: str, review_id: str, request: Request, conn: Conn) -> dict[str, Any]:
    """One recorded assessment, with what it was bound to and what it does not authorize.

    `meansNothingAbout` is served rather than written into the interface, because the limits of a
    review are a property of the record and travel with it: a client that rendered its own list
    would be free to shorten it.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(review_id, what="the review")
    row = conn.execute(
        """
        SELECT id, request_id, reviewer_id, reviewer_role, patch_digest, verification_digest,
               journey_version_id, environment_digest, verdict, observations, limitations,
               used_assistive_technology, assistive_technology_detail, supersedes, submitted_at
        FROM review WHERE id = %s
        """,
        (review_id,),
    ).fetchone()
    if row is None:
        raise not_found()

    superseded_by = conn.execute(
        "SELECT id FROM review WHERE supersedes = %s", (review_id,)
    ).fetchone()

    return {
        "reviewId": str(row["id"]),
        "reviewRequestId": None if row["request_id"] is None else str(row["request_id"]),
        "reviewerId": str(row["reviewer_id"]),
        "reviewerRole": str(row["reviewer_role"]),
        "verdict": str(row["verdict"]),
        "observations": str(row["observations"]),
        "limitations": str(row["limitations"]),
        # Never inferred. "A person accepted this" and "a person accepted this having driven it
        # with a screen reader" are very different claims.
        "usedAssistiveTechnology": bool(row["used_assistive_technology"]),
        "assistiveTechnologyDetail": row["assistive_technology_detail"],
        "boundTo": {
            "patchDigest": str(row["patch_digest"]),
            "verificationDigest": str(row["verification_digest"]),
            "journeyVersionId": str(row["journey_version_id"]),
            "environmentDigest": str(row["environment_digest"]),
        },
        "supersedes": None if row["supersedes"] is None else str(row["supersedes"]),
        # Append-only: neither row is ever updated, so a correction reads as what was said and then
        # what was said instead.
        "supersededBy": None if superseded_by is None else str(superseded_by["id"]),
        "submittedAt": str(row["submitted_at"]),
        "meansNothingAbout": [
            "Whether the application is usable by people with disabilities in general.",
            "Whether any untested journey, reader, reader version or locale behaves the same way.",
            "Permission to merge, deploy, publish or release anything.",
            "The machine outcome, which this does not rewrite and cannot overturn.",
        ],
    }


@router.post("/reviews", status_code=status.HTTP_201_CREATED)
def submit_review(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Record one person's assessment of exact material.

    Both the digests the reviewer looked at and the digests the system currently holds are required
    in the body. That looks redundant and is not: comparing a stored record to itself always agrees,
    so a route that read the current digests for both sides would make the staleness check pass
    unconditionally while appearing to protect something.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PATCH_REVIEW,
        body,
        frozenset(
            {
                "patchDigest",
                "verificationDigest",
                "journeyVersionId",
                "environmentDigest",
                "currentPatchDigest",
                "currentVerificationDigest",
                "patchAuthorId",
                "requestId",
                "supersedes",
                "verdict",
                "observations",
                "limitations",
                "usedAssistiveTechnology",
                "assistiveTechnologyDetail",
            }
        ),
    )

    try:
        submission = reviews.ReviewSubmission(
            verdict=ReviewVerdict(str(body["verdict"])),
            observations=str(body["observations"]),
            limitations=str(body.get("limitations", "")),
            used_assistive_technology=bool(body["usedAssistiveTechnology"]),
            assistive_technology_detail=body.get("assistiveTechnologyDetail"),
        )
    except (KeyError, ValueError) as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "verdict, observations and usedAssistiveTechnology are required. Assistive-technology "
            "use is never inferred: a reviewer who read a transcript and one who drove the page "
            "with a screen reader made different claims.",
            request_id=context.request_id,
        ) from exc
    except reviews.ReviewError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
        ) from exc

    try:
        review_id = reviews.submit_review(
            conn,
            workspace_id=workspace_id,
            reviewer_id=context.principal.user_id,
            reviewer_role=Role(context.principal.role),
            patch_digest=str(body["patchDigest"]),
            verification_digest=str(body["verificationDigest"]),
            journey_version_id=str(body["journeyVersionId"]),
            environment_digest=str(body["environmentDigest"]),
            submission=submission,
            current_patch_digest=str(body["currentPatchDigest"]),
            current_verification_digest=str(body["currentVerificationDigest"]),
            patch_author_id=body.get("patchAuthorId"),
            request_id=body.get("requestId"),
            supersedes=body.get("supersedes"),
        )
    except KeyError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "every digest this review is bound to is required, including the current ones it is "
            "being checked against",
            request_id=context.request_id,
        ) from exc
    except reviews.StaleReviewContext as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc), request_id=context.request_id) from exc
    except reviews.IndependencePolicyViolation as exc:
        raise ProblemDetail(
            ProblemCode.PERMISSION_DENIED, str(exc), request_id=context.request_id
        ) from exc
    except reviews.ReviewError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
        ) from exc

    return {"reviewId": review_id, "verdict": str(submission.verdict)}
