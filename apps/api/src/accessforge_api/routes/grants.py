"""Standing execution grants, and the confirmation a restored one needs.

Five routes. The one worth reading is the last: `POST /execution-grants/{id}/revalidations`.

Module 27's reconciliation marks every grant a restore brings back as requiring revalidation,
because a grant revoked an hour after the snapshot is live in the backup and revoked in the world,
and nothing in that data distinguishes the two. It introduced the flag and no way to clear it, which
left every restored grant permanently unusable -- a fail-closed control with no door. That is worse
than a missing control, because the system looks like it is working right up until a schedule
silently stops firing.

Creating a grant requires `RUN_APPROVE`, not `RUN_REQUEST`. A standing grant is the authority that
acts while nobody is watching; letting anyone who may request a run also mint one that requests runs
forever would make the approval boundary decorative.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size, require_if_match, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_persistence import grants

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["execution-grants"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]

CREATE_FIELDS = frozenset(
    {
        "projectId",
        "environment",
        "allowedJourneyVersionIds",
        "allowedPolicyVersionIds",
        "permittedEffects",
        "actionBudget",
        "wallTimeBudgetSeconds",
        "expiresAt",
    }
)


def _strings(body: dict[str, Any], field: str, request_id: str) -> list[str]:
    """Require a JSON array, because a string is iterable and would pass silently.

    `[str(v) for v in body["allowedJourneyVersionIds"]]` turns `"abc"` into `["a", "b", "c"]` --
    three journey versions named after letters, accepted, stored, and discovered only when a
    schedule fails to match any of them. Every scope field on a grant is a list of identities, and
    a caller who sent one identity instead of a list of one made a mistake worth telling them about.
    """
    value = body.get(field, [])
    if not isinstance(value, list):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"{field} must be an array. A bare string is iterable, so it would be accepted as one "
            "entry per character and stored as a scope nobody meant.",
            request_id=request_id,
        )
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                f"every entry in {field} must be a non-empty string",
                request_id=request_id,
            )
    return [str(v) for v in value]


def _identifiers(body: dict[str, Any], field: str, request_id: str) -> list[str]:
    """The same, and each entry must be a well-formed identifier.

    Checked here rather than left to the database: a malformed UUID reaching a UUID comparison is an
    unhandled driver error and a 500, and these values come straight from a request body.
    """
    return [
        as_identifier(v, what=f"an entry in {field}") for v in _strings(body, field, request_id)
    ]


def _refuse(exc: grants.GrantError) -> ProblemDetail:
    """Map a domain refusal onto the one code that matches it.

    A stale revision is a 409 and everything else here is a 400, because they ask the caller for
    different things: re-read and decide again, versus send a different body.
    """
    if isinstance(exc, grants.StaleGrantRevision):
        return ProblemDetail(ProblemCode.STALE_REVISION, str(exc))
    return ProblemDetail(ProblemCode.INVALID_INPUT, str(exc))


@router.post("/execution-grants", status_code=status.HTTP_201_CREATED)
def create_execution_grant(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any], response: Response
) -> dict[str, Any]:
    """Mint a standing grant. Bounded on every axis, and expiring.

    `RUN_APPROVE` rather than `RUN_REQUEST`: a grant is a machine that requests runs, and somebody
    who may request one run has not been trusted to build one of those.
    """
    body = as_body(payload)
    context = authorize(
        conn, request, workspace_id, Permission.RUN_APPROVE, body=body, allowed_fields=CREATE_FIELDS
    )

    missing = sorted(CREATE_FIELDS - set(body) - {"permittedEffects"})
    if missing:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"a grant must state every bound explicitly; missing: {', '.join(missing)}. There is "
            "no default for any of these, because a default bound is a bound nobody chose.",
            request_id=context.request_id,
        )

    def perform() -> dict[str, Any]:
        try:
            stored = grants.create_grant(
                conn,
                workspace_id=workspace_id,
                project_id=as_identifier(str(body["projectId"]), what="projectId"),
                environment=str(body["environment"]),
                allowed_journey_version_ids=_identifiers(
                    body, "allowedJourneyVersionIds", context.request_id
                ),
                allowed_policy_version_ids=_strings(
                    body, "allowedPolicyVersionIds", context.request_id
                ),
                permitted_effects=_strings(body, "permittedEffects", context.request_id),
                action_budget=int(body["actionBudget"]),
                wall_time_budget_seconds=int(body["wallTimeBudgetSeconds"]),
                expires_at=str(body["expiresAt"]),
            )
        except grants.GrantError as exc:
            raise _refuse(exc) from exc
        except (TypeError, ValueError) as exc:
            raise ProblemDetail(ProblemCode.INVALID_INPUT, f"invalid grant: {exc}") from exc
        return stored.as_dict()

    outcome = run_idempotently(
        conn, context, route="POST /execution-grants", body=body, perform=perform
    )
    result = outcome.response or {}
    response.headers["ETag"] = f'"{result.get("revision", 1)}"'
    return result


@router.get("/execution-grants")
def list_execution_grants(
    workspace_id: str, request: Request, conn: Conn, limit: int | None = None
) -> dict[str, Any]:
    """Every grant, revoked ones included, each saying whether it is usable and why not.

    `unusableBecause` comes from the same `check_usable` the dispatch path calls rather than being
    re-derived here. A listing with its own idea of usability eventually disagrees with the code
    that enforces it, and the disagreement surfaces as a grant this endpoint calls usable and every
    run refuses.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    found = grants.list_grants(conn, workspace_id=workspace_id, limit=clamp_page_size(limit))
    return {
        "items": [g.as_dict() for g in found],
        "meaning": (
            "Revoked grants are listed. The question after an incident is what was allowed to run "
            "and when that stopped, and a listing that hid them would answer only the first half."
        ),
    }


@router.get("/execution-grants/{grant_id}")
def read_execution_grant(
    workspace_id: str, grant_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    try:
        stored = grants.load_grant(conn, grant_id=as_identifier(grant_id, what="grantId"))
    except grants.NoSuchGrant:
        raise not_found() from None
    response.headers["ETag"] = f'"{stored.grant.revision}"'
    return stored.as_dict()


@router.post("/execution-grants/{grant_id}/revoke")
def revoke_execution_grant(
    workspace_id: str, grant_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Withdraw a grant. A revision, never a delete.

    Idempotent: the operator most likely to revoke twice is the one who is not sure the first
    attempt landed, and that is precisely when the second call must not be an error.
    """
    context = authorize(conn, request, workspace_id, Permission.RUN_APPROVE, body={})
    revision = require_if_match(context)
    try:
        stored = grants.revoke_grant(
            conn,
            grant_id=as_identifier(grant_id, what="grantId"),
            expected_revision=revision,
        )
    except grants.NoSuchGrant:
        raise not_found() from None
    except grants.GrantError as exc:
        raise _refuse(exc) from exc
    response.headers["ETag"] = f'"{stored.grant.revision}"'
    return stored.as_dict()


@router.post("/execution-grants/{grant_id}/revalidations", status_code=status.HTTP_201_CREATED)
def revalidate_execution_grant(
    workspace_id: str, grant_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Confirm that a grant a restore brought back is still authorized.

    The door out of `revalidation_required`, which module 27 introduced without one. A restored
    grant was therefore unusable forever, and the failure was silent: schedules simply stopped
    admitting occurrences with a skip reason nobody was reading.

    `RUN_APPROVE`, and the approving user is recorded on the row. "Somebody confirmed this" with no
    name attached is not a confirmation, and this is the one operation in the system whose entire
    value is that a person looked.
    """
    context = authorize(conn, request, workspace_id, Permission.RUN_APPROVE, body={})
    revision = require_if_match(context)
    try:
        stored = grants.revalidate_grant(
            conn,
            grant_id=as_identifier(grant_id, what="grantId"),
            expected_revision=revision,
            revalidated_by=context.principal.user_id,
        )
    except grants.NoSuchGrant:
        raise not_found() from None
    except grants.GrantError as exc:
        raise _refuse(exc) from exc

    response.headers["ETag"] = f'"{stored.grant.revision}"'
    return {
        **stored.as_dict(),
        "confirmed": (
            "You have confirmed that this grant, restored from a backup, is still authorized. That "
            "is a statement about the authorization, not about the runs it will produce: nothing "
            "here revalidates evidence, and a restore does not make an interrupted run complete."
        ),
    }
