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
from accessforge_api.routes._common import as_body, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission, Role
from accessforge_domain.states import FindingStatus, ReviewVerdict
from accessforge_persistence import reviews

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["findings"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]


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
