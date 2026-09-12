"""Proposed repairs, their exact authorization, and what verification established.

Three separations the responses are shaped to keep visible.

**Proposing is not approving.** `POST /findings/{id}/patches` records a change somebody suggests. It
needs `RUN_REQUEST`, the same authority as asking for a run, because that is what it is: a request.
Approving it needs `PATCH_APPROVE`, which is an owner-level permission, because a `PATCH_APPLY`
approval authorizes executing a patch author's code in a candidate workspace.

**Approving is not verifying.** An approval says somebody with authority agreed to try the change.
It
says nothing about whether the change works, and the patch status distinguishes them by name.

**Verified is not accessible, and not compliant.** Every verification response carries `meaning`
saying what the conclusion covers: this assertion, this journey, this reader profile. A product that
let `VERIFIED` be read as "the application is accessible" would be the most expensive kind of wrong.

There is deliberately no route that sets a verification's conclusion. The conclusion is derived from
recorded evidence, and a route accepting one would be the hole CONTRACTS closes when it says human
review cannot convert an inconclusive candidate to verified.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import require_if_match, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import PatchStatus
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import patches

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["patches"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]

PROPOSE_FIELDS = frozenset(
    {
        "baseManifestDigest",
        "baseSourceDigest",
        "changes",
        "rationale",
        "applicationPaths",
        "acknowledgeSeparateReview",
    }
)
APPROVE_FIELDS = frozenset({"expiresInSeconds"})
VERIFY_FIELDS = frozenset({"baselineRunId", "baselineIdentity"})

#: How long a PATCH_APPLY approval lasts unless the caller shortens it. Bounded rather than
#: open-ended: an approval that never expires is a standing permission to run somebody's patch, and
#: the product's authorization model is built on exact, perishable decisions.
DEFAULT_APPROVAL_SECONDS = 3600
MAX_APPROVAL_SECONDS = 86_400


def _changes_from(body: dict[str, Any], *, request_id: str | None) -> tuple[ProposedChange, ...]:
    """Read the proposed changes, refusing anything that is not a list of objects.

    A bare string here would be iterated one character at a time and produce a patch of
    single-character paths -- refused by the policy, but refused for the wrong reason and reported
    as a policy problem rather than a malformed request.
    """
    raw = body.get("changes")
    if not isinstance(raw, list) or not raw or not all(isinstance(c, dict) for c in raw):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "changes must be a non-empty array of objects, each with a path and the content after "
            "the change. A patch that changes nothing cannot be reviewed, and a bare string would "
            "be read as one path per character.",
            request_id=request_id,
        )
    out: list[ProposedChange] = []
    for entry in raw:
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                "every change needs a path. A change with none names no file a reviewer could "
                "read.",
                request_id=request_id,
            )
        content = entry.get("content")
        if content is not None and not isinstance(content, str):
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                f"the content for {path} must be a string, or null for a deletion.",
                request_id=request_id,
            )
        mode = entry.get("mode")
        out.append(
            ProposedChange(
                path=path,
                content=content,
                mode=None if mode is None else str(mode),
                binary=bool(entry.get("binary", False)),
            )
        )
    return tuple(out)


def _patch_view(patch: patches.PatchProposal) -> dict[str, Any]:
    return {
        "patchId": patch.patch_id,
        "findingId": patch.finding_id,
        "status": str(patch.status),
        "baseManifestDigest": patch.base_manifest_digest,
        "baseSourceDigest": patch.base_source_digest,
        "patchDigest": patch.patch_digest,
        "changedPaths": list(patch.changed_paths),
        # The bytes themselves, because a reviewer approving a patch has to be able to read it. A
        # response listing only filenames asks somebody to authorize a change they cannot see.
        "changes": [
            {
                "path": c.path,
                "operation": "DELETE" if c.content is None else "MODIFY",
                "content": c.content,
                "mode": c.mode,
                "binary": c.binary,
            }
            for c in patch.changes
        ],
        # Surfaced at the top level, not buried in the diff. A reviewer told only "fixed the label"
        # would not know a lockfile moved, and that changes what the candidate is built from.
        "separatelyReviewedPaths": list(patch.separately_reviewed_paths),
        "approvalId": patch.approval_id,
        "proposedBy": patch.proposed_by,
        "rationale": patch.rationale,
        "revision": patch.revision,
        "createdAt": patch.created_at,
        "meaning": patch.meaning,
    }


def _verification_view(record: patches.VerificationRecord) -> dict[str, Any]:
    return {
        "verificationId": record.verification_id,
        "patchId": record.patch_id,
        "baselineRunId": record.baseline_run_id,
        "candidateRunId": record.candidate_run_id,
        "state": record.state,
        "conclusion": record.conclusion,
        # Always populated once concluded, and the reason a reader can act on. "Not verified" tells
        # nobody anything; "the candidate ran against a different reader profile" does.
        "reasons": list(record.reasons),
        "revision": record.revision,
        "createdAt": record.created_at,
        "meaning": record.meaning,
    }


@router.post("/findings/{finding_id}/patches", status_code=status.HTTP_201_CREATED)
def propose_patch(
    workspace_id: str,
    finding_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
    response: Response,
) -> dict[str, Any]:
    """Record a proposed repair for a finding.

    `RUN_REQUEST`, not `PATCH_APPROVE`. Proposing a change is asking, and the answer is a separate
    act by a separate authority -- which is the whole point of having two permissions.

    A 201 here means the proposal was recorded and the policy allowed its paths. It does not mean
    anybody has agreed to apply it, and `status` says `PROPOSED` to make that unmissable.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.RUN_REQUEST,
        body=body,
        allowed_fields=PROPOSE_FIELDS,
    )
    as_identifier(finding_id, what="findingId")
    if conn.execute("SELECT 1 FROM finding WHERE id = %s", (finding_id,)).fetchone() is None:
        raise not_found()

    rationale = body.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "a rationale is required and must be a non-empty string. It is what a reviewer reads "
            "before the diff, and str(None) would store the word 'None' as the reason for a change "
            "to somebody's application.",
            request_id=context.request_id,
        )
    for field in ("baseManifestDigest", "baseSourceDigest"):
        value = body.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                f"{field} must be a 64-character hex digest. The patch is recorded against an "
                "exact base identity, and an approval binds to it; a missing half would let the "
                "source move underneath the approval unnoticed.",
                request_id=context.request_id,
            )

    application_paths = body.get("applicationPaths") or []
    if not isinstance(application_paths, list) or not all(
        isinstance(p, str) for p in application_paths
    ):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "applicationPaths must be an array of path prefixes naming the repair surface.",
            request_id=context.request_id,
        )

    changes = _changes_from(body, request_id=context.request_id)

    def perform() -> dict[str, Any]:
        try:
            patch = patches.propose_patch(
                conn,
                workspace_id=workspace_id,
                finding_id=finding_id,
                base_manifest_digest=str(body["baseManifestDigest"]),
                base_source_digest=str(body["baseSourceDigest"]),
                changes=changes,
                rationale=rationale,
                proposed_by=context.principal.user_id,
                application_paths=tuple(str(p) for p in application_paths),
                acknowledge_separate_review=bool(body.get("acknowledgeSeparateReview", False)),
            )
        except patches.PatchRefused as exc:
            # INVALID_INPUT, not a permission error. The caller's authority was fine; the change is
            # not one this product will carry. The text names every path and says why, because a
            # refusal a caller cannot act on becomes one who tries variations until one passes.
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
            ) from exc
        except patches.PatchError as exc:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
            ) from exc
        return _patch_view(patch)

    outcome = run_idempotently(
        conn, context, route="POST /findings/patches", body=body, perform=perform
    )
    if outcome.replayed:
        response.headers["Idempotent-Replay"] = "true"
    return outcome.response or {}


@router.get("/patches/{patch_id}")
def get_patch(
    workspace_id: str, patch_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(patch_id, what="patchId")
    try:
        patch = patches.load_patch(conn, patch_id=patch_id)
    except patches.PatchError as exc:
        raise not_found() from exc
    response.headers["ETag"] = f'"{patch.revision}"'
    return _patch_view(patch)


@router.get("/findings/{finding_id}/patches")
def list_patches_for_finding(
    workspace_id: str, finding_id: str, request: Request, conn: Conn
) -> dict[str, Any]:
    """Every patch proposed for this finding, including the rejected and stale ones.

    A finding that took four attempts is a different story from one that took one, and showing only
    the survivor would tell the second story.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(finding_id, what="findingId")
    if conn.execute("SELECT 1 FROM finding WHERE id = %s", (finding_id,)).fetchone() is None:
        raise not_found()
    return {
        "items": [_patch_view(p) for p in patches.patches_for_finding(conn, finding_id=finding_id)]
    }


@router.post("/patches/{patch_id}/approval", status_code=status.HTTP_201_CREATED)
def approve_patch(
    workspace_id: str,
    patch_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Issue a `PATCH_APPLY` approval for this exact patch and attach it.

    `PATCH_APPROVE`, which owners and maintainers hold and reviewers do not. A reviewer's job is to
    assess evidence; authorizing the execution of a patch author's code in a candidate workspace is
    a
    different decision.

    `If-Match` is required. This is a decision about the patch as the approver read it: if somebody
    amended it in between, the approval would bind to bytes the approver never saw.

    The approval is minted here rather than accepted as a client-supplied id, so that its
    `target_digest` is this patch's digest and its `expected_revision` is this patch's revision by
    construction. A caller-supplied approval could name anything.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PATCH_APPROVE,
        body=body,
        allowed_fields=APPROVE_FIELDS,
    )
    expected = require_if_match(context)
    as_identifier(patch_id, what="patchId")
    try:
        patch = patches.load_patch(conn, patch_id=patch_id)
    except patches.PatchError as exc:
        raise not_found() from exc
    if patch.revision != expected:
        raise ProblemDetail(
            ProblemCode.STALE_REVISION,
            f"the patch is at revision {patch.revision}, not {expected}. It changed since you read "
            "it, and an approval binding to the version you saw would authorize different bytes.",
            request_id=context.request_id,
        )

    seconds = body.get("expiresInSeconds", DEFAULT_APPROVAL_SECONDS)
    if (
        not isinstance(seconds, int)
        or isinstance(seconds, bool)
        or not 0 < seconds <= MAX_APPROVAL_SECONDS
    ):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"expiresInSeconds must be a whole number of seconds between 1 and "
            f"{MAX_APPROVAL_SECONDS}. An approval that never expires is a standing permission to "
            "run somebody's patch, which this product does not issue.",
            request_id=context.request_id,
        )

    now = datetime.now(UTC)
    try:
        # Minted inside `approve_patch`, after the transition, so the approval binds to the revision
        # the patch has once approved. Minting here beforehand recorded the pre-transition number,
        # and the dispatch check had to compare the approval against itself to keep working.
        approved = patches.approve_patch(
            conn,
            workspace_id=workspace_id,
            patch_id=patch_id,
            actor_id=context.principal.user_id,
            expires_at=to_rfc3339_utc(now + timedelta(seconds=seconds)),
            expected_revision=expected,
            now=now,
        )
    except patches.PatchError as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc), request_id=context.request_id) from exc

    view = _patch_view(approved)
    view["meaning"] = (
        "Approved to be applied in an isolated candidate workspace, until "
        f"{to_rfc3339_utc(now + timedelta(seconds=seconds))}. This is not authorization to merge, "
        "publish or deploy -- no scope in this product grants that -- and it establishes nothing "
        "about whether the change works."
    )
    return view


@router.post("/patches/{patch_id}/verifications", status_code=status.HTTP_201_CREATED)
def open_verification(
    workspace_id: str,
    patch_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Open a verification linking this patch to the baseline run it must improve on.

    The baseline is required here rather than supplied at conclusion. A verification that collected
    a
    baseline after seeing the candidate could choose a baseline that makes the candidate look good,
    and the comparison would be a selection rather than a test.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PATCH_APPROVE,
        body=body,
        allowed_fields=VERIFY_FIELDS,
    )
    as_identifier(patch_id, what="patchId")
    baseline = body.get("baselineRunId")
    if not isinstance(baseline, str):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "baselineRunId is required: a verification with nothing to compare against is a "
            "candidate run with opinions.",
            request_id=context.request_id,
        )
    as_identifier(baseline, what="baselineRunId")
    identity = body.get("baselineIdentity") or {}
    if not isinstance(identity, dict):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "baselineIdentity must be an object recording the browser, reader, evaluator, fixture "
            "and locale the baseline ran under. Unexplained drift in any of them disqualifies the "
            "pair (INV-04), which cannot be checked against a value that was never recorded.",
            request_id=context.request_id,
        )

    try:
        record = patches.open_verification(
            conn,
            workspace_id=workspace_id,
            patch_id=patch_id,
            baseline_run_id=baseline,
            baseline_identity=identity,
        )
    except patches.VerificationError as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc), request_id=context.request_id) from exc
    except patches.PatchError as exc:
        raise not_found() from exc

    view = _verification_view(record)
    view["runtimeProof"] = (
        "No candidate has been built or run. This deployment has no containment boundary for "
        "executing an application's build and no real screen reader attached, so a candidate "
        "cannot be produced here -- and VERIFIED requires one. The record exists so the gates are "
        "in place when a runner is."
    )
    return view


@router.get("/verifications/{verification_id}")
def get_verification(
    workspace_id: str, verification_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """A read-only verification summary.

    Read-only for everyone, including reviewers and including owners. The conclusion is derived from
    recorded evidence; there is no route that writes it, because a frozen outcome that a
    sufficiently
    senior caller can edit is not frozen.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(verification_id, what="verificationId")
    try:
        record = patches.load_verification(conn, verification_id=verification_id)
    except patches.VerificationError as exc:
        raise not_found() from exc
    response.headers["ETag"] = f'"{record.revision}"'
    return _verification_view(record)


@router.get("/patches/{patch_id}/verifications")
def list_verifications(
    workspace_id: str, patch_id: str, request: Request, conn: Conn
) -> dict[str, Any]:
    """Every verification attempted for this patch, oldest first.

    All attempts, never a selected one. Reporting every attempt rather than the best is what
    separates a verification from a search for a favourable result.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(patch_id, what="patchId")
    try:
        patches.load_patch(conn, patch_id=patch_id)
    except patches.PatchError as exc:
        raise not_found() from exc
    records = patches.verifications_for_patch(conn, patch_id=patch_id)
    return {
        "items": [_verification_view(r) for r in records],
        "meaning": (
            "Every verification attempted for this patch, including the ones that established "
            "nothing. A patch is repaired when a matched pair says so, not when one of several "
            "attempts does."
        ),
    }


@router.post("/patches/{patch_id}/rejection")
def reject_patch(
    workspace_id: str,
    patch_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Reject a proposed patch with a stated reason.

    `PATCH_REVIEW`, which reviewers hold: declining a change is assessment, not authorization. The
    reason is required and recorded in the patch's history, because "rejected" with no reason is a
    dead end for whoever proposes the next one.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PATCH_REVIEW,
        body=body,
        allowed_fields=frozenset({"reason"}),
    )
    as_identifier(patch_id, what="patchId")
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "a reason is required. 'Rejected' on its own leaves whoever proposes the next patch "
            "guessing at what was wrong with this one.",
            request_id=context.request_id,
        )
    try:
        patch = patches.transition_patch(
            conn,
            workspace_id=workspace_id,
            patch_id=patch_id,
            to_status=PatchStatus.REJECTED,
            actor_id=context.principal.user_id,
            reason=reason,
        )
    except patches.PatchError as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc), request_id=context.request_id) from exc
    return _patch_view(patch)
