"""Tester Desk routes: the commercial retest surface.

Three audiences share one prefix but not one authority source:

* **Agency side** (``/commissions``, ``/clients``, ``/consent-terms``, ``/submissions``,
  ``/payables``, ``/reports``) resolves workspace membership and a named permission, exactly like
  every other route in this service.
* **Tester side** (``/tester/...``) resolves the session, then a *tester roster enrollment* in this
  workspace. Testers are deliberately not workspace members: membership would grant the
  accessibility product's evidence, which a consenting contractor must never see by default.
* **Client side** (``/client/...``) resolves the session, then a *client viewer grant* bound to the
  commission's client identity. Reports exist only per issued, redacted revision.

All three honour the same CSRF, idempotency and rate-limit plumbing; only the authority lookup
differs.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response, status

from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE, verify_csrf
from accessforge_api.auth.sessions import resolve_session
from accessforge_api.dependencies import (
    enforce_write_rate_limit,
    run_idempotently,
)
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import (
    as_body,
    as_identifier,
    authorize,
    workspace_scope,
)
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.tester_desk import BriefError
from accessforge_domain.timestamps import TimestampError, parse_rfc3339_utc
from accessforge_persistence import tester_desk as td

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/tester-desk", tags=["tester-desk"])
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


def _request_id(request: Request) -> str:
    from accessforge_api.telemetry import resolve_request_id

    return resolve_request_id(request)


@dataclass(frozen=True, slots=True)
class _Actor:
    """A person acting through a tester-desk grant rather than workspace membership."""

    user_id: str
    workspace_id: str
    session_id: str

    def permits(self, permission: Permission) -> bool:  # noqa: ARG002
        """Grant-based actors hold no workspace permissions by construction."""
        return False


@dataclass(frozen=True, slots=True)
class ActorContext:
    """The tester/client counterpart of `RequestContext` (see `IdempotentCaller`)."""

    principal: _Actor
    workspace_id: str
    request_id: str
    idempotency_key: str | None
    if_match: int | None


def _resolve_session_for_actor(conn: psycopg.Connection[Any], request: Request) -> tuple[Any, str]:
    request_id = _request_id(request)
    try:
        session = resolve_session(conn, session_token=request.cookies.get(SESSION_COOKIE))
    except Exception as exc:
        raise ProblemDetail(ProblemCode.NOT_AUTHENTICATED, str(exc), request_id=request_id) from exc
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            verify_csrf(session, method=request.method, csrf_token=request.headers.get(CSRF_HEADER))
        except Exception as exc:
            raise ProblemDetail(ProblemCode.CSRF_REQUIRED, str(exc), request_id=request_id) from exc
    return session, request_id


def tester_context(
    conn: psycopg.Connection[Any],
    request: Request,
    workspace_id: str,
) -> tuple[ActorContext, dict[str, Any]]:
    """Resolve a roster-enrolled tester for this workspace, or refuse.

    Authentication and CSRF are identical to `build_context`; authority then comes from the
    roster row, not membership. A signed-in person with no enrollment gets 404 for the same reason
    a non-member gets 404 on workspace routes: the roster's existence is not public information.
    """
    session, request_id = _resolve_session_for_actor(conn, request)
    enrollment = td.enrollment_for_user(conn, workspace_id=workspace_id, user_id=session.user_id)
    if enrollment is None:
        raise not_found()
    enforce_write_rate_limit(
        request,
        principal_id=session.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    )
    context = ActorContext(
        principal=_Actor(
            user_id=session.user_id,
            workspace_id=workspace_id,
            session_id=session.session_id,
        ),
        workspace_id=workspace_id,
        request_id=request_id,
        idempotency_key=request.headers.get("Idempotency-Key"),
        if_match=None,
    )
    return context, enrollment


def client_context(
    conn: psycopg.Connection[Any],
    request: Request,
    workspace_id: str,
) -> tuple[ActorContext, list[str]]:
    """Resolve a signed-in person holding at least one client viewer grant here.

    Returns the context and the granted client ids; report routes then check the report's client
    is among them, so a viewer of one client sees nothing of another (INV-06).
    """
    session, request_id = _resolve_session_for_actor(conn, request)
    grants = td.client_grant_for_user(conn, workspace_id=workspace_id, user_id=session.user_id)
    if not grants:
        raise not_found()
    context = ActorContext(
        principal=_Actor(
            user_id=session.user_id,
            workspace_id=workspace_id,
            session_id=session.session_id,
        ),
        workspace_id=workspace_id,
        request_id=request_id,
        idempotency_key=request.headers.get("Idempotency-Key"),
        if_match=None,
    )
    return context, grants


def _refused(exc: Exception) -> ProblemDetail:
    return ProblemDetail(ProblemCode.CONFLICT, str(exc))


# --- clients -------------------------------------------------------------------------------------


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def create_client(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    authorize(
        conn,
        request,
        workspace_id,
        Permission.TESTER_DESK_ADMIN,
        body=body,
        allowed_fields=frozenset({"name"}),
    )
    try:
        view = td.create_client(conn, workspace_id=workspace_id, name=str(body.get("name") or ""))
    except td.CommissionError as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/clients")
def list_clients(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.TESTER_DESK_ADMIN)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.list_clients(conn, workspace_id=workspace_id)}


@router.post("/clients/{client_id}/viewers", status_code=status.HTTP_201_CREATED)
def grant_viewer(
    workspace_id: str,
    client_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.TESTER_DESK_ADMIN,
        body=body,
        allowed_fields=frozenset({"userId"}),
    )
    as_identifier(client_id, what="client")
    try:
        view = td.grant_client_viewer(
            conn,
            workspace_id=workspace_id,
            client_id=client_id,
            user_id=body.get("userId", ""),
            granted_by=context.principal.user_id,
        )
    except td.CommissionError as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


@router.delete("/clients/{client_id}/viewers/{user_id}")
def revoke_viewer(
    workspace_id: str,
    client_id: str,
    user_id: str,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.TESTER_DESK_ADMIN)
    as_identifier(client_id, what="client")
    as_identifier(user_id, what="user")
    try:
        view = td.revoke_client_viewer(
            conn, workspace_id=workspace_id, client_id=client_id, user_id=user_id
        )
    except td.CommissionError as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


# --- consent terms --------------------------------------------------------------------------------


@router.post("/consent-terms", status_code=status.HTTP_201_CREATED)
def publish_terms(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.TESTER_DESK_ADMIN,
        body=body,
        allowed_fields=frozenset({"body"}),
    )
    try:
        view = td.publish_consent_terms(
            conn,
            workspace_id=workspace_id,
            body=body.get("body", ""),
            actor_id=context.principal.user_id,
        )
    except td.CommissionError as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/consent-terms")
def read_terms(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.TESTER_DESK_ADMIN)
    terms = td.consent_terms(conn, workspace_id=workspace_id, version=version)
    if terms is None:
        raise not_found()
    response.headers["Cache-Control"] = "no-store"
    return terms


# --- commissions ------------------------------------------------------------


_COMMISSION_FIELDS = frozenset(
    {
        "clientId",
        "reference",
        "defectKey",
        "predecessorCommissionId",
        "offerExpiresAt",
        "brief",
    }
)

_BRIEF_FIELDS = frozenset(
    {
        "releaseLabel",
        "releaseDigest",
        "targetUrl",
        "accessNote",
        "taskSteps",
        "environment",
        "rubric",
        "permittedEvidence",
        "feeMinor",
        "currency",
        "consentTermsVersion",
        "deadline",
    }
)


def _extract_brief(body: dict[str, Any]) -> dict[str, Any]:
    brief = body.get("brief")
    if not isinstance(brief, dict):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "a brief object is required")
    unexpected = sorted(set(brief) - _BRIEF_FIELDS)
    if unexpected:
        raise ProblemDetail(
            ProblemCode.UNEXPECTED_FIELD,
            f"the brief contains fields this route does not accept: {', '.join(unexpected)}",
        )
    return {
        "release_label": brief.get("releaseLabel"),
        "release_digest": brief.get("releaseDigest"),
        "target_url": brief.get("targetUrl"),
        "task_steps": brief.get("taskSteps"),
        "environment": brief.get("environment"),
        "rubric": brief.get("rubric"),
        "permitted_evidence": brief.get("permittedEvidence"),
        "fee_minor": brief.get("feeMinor"),
        "currency": brief.get("currency"),
        "consent_terms_version": brief.get("consentTermsVersion"),
        "deadline": brief.get("deadline"),
        "accessNote": brief.get("accessNote"),
    }


@router.post("/commissions", status_code=status.HTTP_201_CREATED)
def create_commission(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.COMMISSION_AUTHOR,
        body=body,
        allowed_fields=_COMMISSION_FIELDS,
    )
    brief = _extract_brief(body)
    try:
        view = td.create_commission(
            conn,
            workspace_id=workspace_id,
            actor_id=context.principal.user_id,
            client_id=body.get("clientId", ""),
            reference=body.get("reference", ""),
            brief=brief,
            defect_key=body.get("defectKey"),
            predecessor_commission_id=body.get("predecessorCommissionId"),
            offer_expires_at=body.get("offerExpiresAt"),
        )
    except BriefError as exc:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, str(exc)) from exc
    except (td.CommissionError, td.EnrollmentError) as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/commissions")
def list_commissions(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_AUTHOR)
    td.expire_offers(conn, workspace_id=workspace_id)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.list_commissions(conn, workspace_id=workspace_id, status=status)}


@router.get("/commissions/{commission_id}")
def read_commission(
    workspace_id: str, commission_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_AUTHOR)
    as_identifier(commission_id, what="commission")
    try:
        view = td.read_commission(conn, workspace_id=workspace_id, commission_id=commission_id)
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/commissions/{commission_id}/briefs/{revision}")
def read_brief(
    workspace_id: str,
    commission_id: str,
    revision: int,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    """Reopen the exact pinned brief version (T-001)."""
    authorize(conn, request, workspace_id, Permission.COMMISSION_AUTHOR)
    as_identifier(commission_id, what="commission")
    try:
        view = td.read_brief_revision(
            conn,
            workspace_id=workspace_id,
            commission_id=commission_id,
            revision=revision,
            include_access_note=True,
        )
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.post("/commissions/{commission_id}/revisions", status_code=status.HTTP_201_CREATED)
def author_revision(
    workspace_id: str,
    commission_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.COMMISSION_AUTHOR,
        body=body,
        allowed_fields=frozenset({"brief"}),
    )
    as_identifier(commission_id, what="commission")
    brief = _extract_brief(body)
    try:
        view = td.author_brief_revision(
            conn,
            workspace_id=workspace_id,
            commission_id=commission_id,
            actor_id=context.principal.user_id,
            brief=brief,
        )
    except LookupError:
        raise not_found() from None
    except BriefError as exc:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, str(exc)) from exc
    except (td.CommissionError, td.EnrollmentError) as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


@router.post("/commissions/{commission_id}/publish")
def publish_commission(
    workspace_id: str,
    commission_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_AUTHOR)
    as_identifier(commission_id, what="commission")
    body = payload or {}
    if set(body) - {"offerExpiresAt"}:
        raise ProblemDetail(ProblemCode.UNEXPECTED_FIELD, "publish accepts only offerExpiresAt")
    try:
        expiration = (
            parse_rfc3339_utc(body["offerExpiresAt"], field="offerExpiresAt")
            if "offerExpiresAt" in body
            else None
        )
        view = td.publish_commission(
            conn,
            workspace_id=workspace_id,
            commission_id=commission_id,
            offer_expires_at=expiration,
        )
    except TimestampError as exc:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, str(exc)) from exc
    except LookupError:
        raise not_found() from None
    except td.CommissionError as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


# --- tester roster (agency side) ------------------------------------------------------------------


@router.post("/testers", status_code=status.HTTP_201_CREATED)
def enroll_tester(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.TESTER_DESK_ADMIN,
        body=body,
        allowed_fields=frozenset(
            {"userId", "displayName", "capabilities", "payoutReference", "conflicts"}
        ),
    )
    try:
        view = td.enroll_tester(
            conn,
            workspace_id=workspace_id,
            actor_id=context.principal.user_id,
            user_id=body.get("userId", ""),
            display_name=body.get("displayName", ""),
            capabilities=body.get("capabilities") or {},
            payout_reference=body.get("payoutReference"),
            conflicts=body.get("conflicts"),
        )
    except (td.EnrollmentError, BriefError, ValueError) as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/testers")
def list_testers(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.TESTER_DESK_ADMIN)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.list_testers(conn, workspace_id=workspace_id)}


@router.post("/testers/{tester_id}/status")
def change_tester_status(
    workspace_id: str,
    tester_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    authorize(
        conn,
        request,
        workspace_id,
        Permission.TESTER_DESK_ADMIN,
        body=body,
        allowed_fields=frozenset({"status", "reason"}),
    )
    as_identifier(tester_id, what="tester")
    try:
        view = td.set_tester_status(
            conn,
            workspace_id=workspace_id,
            tester_id=tester_id,
            status=body.get("status", ""),
            reason=body.get("reason", ""),
        )
    except LookupError:
        raise not_found() from None
    except td.EnrollmentError as exc:
        raise _refused(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return view


# --- tester side: offers, claims and assignments ----------------------------------------------


@router.get("/tester/offers")
def tester_offers(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    response.headers["Cache-Control"] = "no-store"
    items = td.offers_for_tester(conn, workspace_id=workspace_id, enrollment=enrollment)
    for offer in items:
        # Pin the terms text on every offer: a tester cannot be asked to accept
        # consent the inbox never shows them.
        offer["consentTerms"] = td.consent_terms(
            conn,
            workspace_id=workspace_id,
            version=offer["brief"]["consentTermsVersion"],
        )
    return {"items": items}


@router.get("/tester/offers/{commission_id}")
def tester_offer_detail(
    workspace_id: str, commission_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(commission_id, what="commission")
    offers = td.offers_for_tester(conn, workspace_id=workspace_id, enrollment=enrollment)
    offer = next((o for o in offers if o["commissionId"] == commission_id), None)
    if offer is None:
        raise not_found()
    terms = td.consent_terms(
        conn,
        workspace_id=workspace_id,
        version=offer["brief"]["consentTermsVersion"],
    )
    offer["consentTerms"] = terms
    response.headers["Cache-Control"] = "no-store"
    return offer


@router.post("/tester/offers/{commission_id}/claim", status_code=status.HTTP_201_CREATED)
def claim_offer(
    workspace_id: str,
    commission_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(commission_id, what="commission")
    unexpected = sorted(set(body) - {"consentTermsVersion", "consentAccepted"})
    if unexpected:
        raise ProblemDetail(
            ProblemCode.UNEXPECTED_FIELD,
            f"the request body contains fields this route does not accept: {', '.join(unexpected)}",
        )

    def perform() -> dict[str, Any]:
        try:
            return td.claim_offer(
                conn,
                workspace_id=workspace_id,
                commission_id=commission_id,
                enrollment=enrollment,
                consent_terms_version=body.get("consentTermsVersion"),
                consent_accepted=body.get("consentAccepted"),
            )
        except LookupError:
            raise not_found() from None
        except td.EnrollmentError as exc:
            raise _refused(exc) from exc

    result = run_idempotently(
        conn,
        context,
        route=f"POST /tester-desk/tester/offers/{commission_id}/claim",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.get("/tester/assignments")
def tester_assignments(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": td.assignments_for_tester(
            conn, workspace_id=workspace_id, tester_id=enrollment["testerId"]
        )
    }


@router.get("/tester/assignments/{assignment_id}")
def tester_assignment(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    try:
        view = td.assignment_for_tester(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.post("/tester/assignments/{assignment_id}/withdraw")
def withdraw_assignment(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    try:
        view = td.withdraw_assignment(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


# --- tester side: evidence capture ------------------------------------------------


@router.get("/tester/assignments/{assignment_id}/draft")
def tester_draft(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    try:
        td.assignment_for_tester(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
        view = td.read_draft(conn, workspace_id=workspace_id, assignment_id=assignment_id)
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.put("/tester/assignments/{assignment_id}/draft")
def save_draft(
    workspace_id: str,
    assignment_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    unexpected = sorted(set(body) - {"observations", "baseRevision"})
    if unexpected:
        raise ProblemDetail(
            ProblemCode.UNEXPECTED_FIELD,
            f"the request body contains fields this route does not accept: {', '.join(unexpected)}",
        )

    def perform() -> dict[str, Any]:
        try:
            return td.save_draft(
                conn,
                workspace_id=workspace_id,
                assignment_id=assignment_id,
                tester_id=enrollment["testerId"],
                observations=body.get("observations"),
                base_revision=body.get("baseRevision"),
            )
        except LookupError:
            raise not_found() from None
        except td.EvidenceError as exc:
            raise ProblemDetail(ProblemCode.CONFLICT, str(exc)) from exc

    result = run_idempotently(
        conn,
        context,
        route=f"PUT /tester-desk/tester/assignments/{assignment_id}/draft",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.post(
    "/tester/assignments/{assignment_id}/attachments",
    status_code=status.HTTP_201_CREATED,
)
def upload_attachment(
    workspace_id: str,
    assignment_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    unexpected = sorted(set(body) - {"kind", "contentType", "filename", "contentBase64"})
    if unexpected:
        raise ProblemDetail(
            ProblemCode.UNEXPECTED_FIELD,
            f"the request body contains fields this route does not accept: {', '.join(unexpected)}",
        )
    encoded = body.get("contentBase64")
    if not isinstance(encoded, str):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "contentBase64 must be base64 text")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "contentBase64 is not valid base64"
        ) from None

    def perform() -> dict[str, Any]:
        try:
            return td.add_attachment(
                conn,
                workspace_id=workspace_id,
                assignment_id=assignment_id,
                tester_id=enrollment["testerId"],
                kind=body.get("kind"),
                content_type=body.get("contentType"),
                filename=body.get("filename"),
                content=content,
            )
        except LookupError:
            raise not_found() from None
        except td.EvidenceError as exc:
            raise ProblemDetail(ProblemCode.INVALID_INPUT, str(exc)) from exc

    result = run_idempotently(
        conn,
        context,
        route=f"POST /tester-desk/tester/assignments/{assignment_id}/attachments",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.get("/tester/assignments/{assignment_id}/attachments")
def list_attachments(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    try:
        td.assignment_for_tester(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": td.list_attachments(conn, workspace_id=workspace_id, assignment_id=assignment_id)
    }


@router.get("/tester/assignments/{assignment_id}/attachments/{attachment_id}/content")
def attachment_content(
    workspace_id: str,
    assignment_id: str,
    attachment_id: str,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    as_identifier(attachment_id, what="attachment")
    try:
        td.assignment_for_tester(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
        view = td.attachment_content(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            attachment_id=attachment_id,
        )
    except LookupError:
        raise not_found() from None
    except td.EvidenceError as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {
        "attachmentId": view["attachmentId"],
        "contentType": view["contentType"],
        "filename": view["filename"],
        "sha256": view["sha256"],
        "contentBase64": base64.b64encode(view["content"]).decode("ascii"),
    }


@router.get("/tester/assignments/{assignment_id}/progress")
def assignment_progress(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    try:
        td.assignment_for_tester(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
        view = td.evidence_progress(conn, workspace_id=workspace_id, assignment_id=assignment_id)
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.post("/tester/assignments/{assignment_id}/submit")
def submit_assignment(
    workspace_id: str,
    assignment_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    if body:
        raise ProblemDetail(
            ProblemCode.UNEXPECTED_FIELD,
            "the request body contains fields this route does not accept: "
            + ", ".join(sorted(body)),
        )

    def perform() -> dict[str, Any]:
        try:
            return td.submit_assignment(
                conn,
                workspace_id=workspace_id,
                assignment_id=assignment_id,
                tester_id=enrollment["testerId"],
            )
        except LookupError:
            raise not_found() from None
        except td.ReviewError as exc:
            raise _refused(exc) from exc

    result = run_idempotently(
        conn,
        context,
        route=f"POST /tester-desk/tester/assignments/{assignment_id}/submit",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.get("/tester/assignments/{assignment_id}/submissions")
def tester_submissions(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    try:
        td.assignment_for_tester(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": td.list_submissions(conn, workspace_id=workspace_id, assignment_id=assignment_id)
    }


# --- agency review ----------------------------------------------------------------------------


_REVIEW_DECISION_FIELDS = frozenset({"submissionDigest", "decision", "justification"})


@router.get("/review/queue")
def review_queue(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_REVIEW)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.review_queue(conn, workspace_id=workspace_id)}


@router.get("/review/assignments/{assignment_id}")
def review_assignment(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_REVIEW)
    as_identifier(assignment_id, what="assignment")
    try:
        view = td.read_assignment(conn, workspace_id=workspace_id, assignment_id=assignment_id)
    except LookupError:
        raise not_found() from None
    view.pop("brief", None) or None
    # The brief (including its access note) stays scoped to the owner; the reviewer works from
    # the sealed submission. Attach the pinned public terms minus the access note.
    brief = td.read_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=view["commissionId"],
        revision=view["briefRevision"],
        include_access_note=False,
    )
    view["brief"] = brief
    view["submissions"] = td.list_submissions(
        conn, workspace_id=workspace_id, assignment_id=assignment_id
    )
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/review/assignments/{assignment_id}/attachments/{attachment_id}/content")
def review_attachment_content(
    workspace_id: str,
    assignment_id: str,
    attachment_id: str,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_REVIEW)
    as_identifier(assignment_id, what="assignment")
    as_identifier(attachment_id, what="attachment")
    try:
        view = td.attachment_content(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            attachment_id=attachment_id,
        )
    except LookupError:
        raise not_found() from None
    except td.EvidenceError as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {
        "attachmentId": view["attachmentId"],
        "kind": view["kind"],
        "contentType": view["contentType"],
        "filename": view["filename"],
        "sha256": view["sha256"],
        "contentBase64": base64.b64encode(view["content"]).decode("ascii"),
    }


@router.post("/review/assignments/{assignment_id}/decision")
def review_decision(
    workspace_id: str,
    assignment_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.COMMISSION_REVIEW,
        body=body,
        allowed_fields=_REVIEW_DECISION_FIELDS,
    )
    as_identifier(assignment_id, what="assignment")

    def perform() -> dict[str, Any]:
        try:
            return td.decide_submission(
                conn,
                workspace_id=workspace_id,
                assignment_id=assignment_id,
                submission_hash=body.get("submissionDigest"),
                decision=body.get("decision"),
                justification=body.get("justification"),
                decided_by=context.principal.user_id,
            )
        except LookupError:
            raise not_found() from None
        except td.ReviewError as exc:
            raise _refused(exc) from exc

    result = run_idempotently(
        conn,
        context,
        route=f"POST /tester-desk/review/assignments/{assignment_id}/decision",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


# --- compensation ----------------------------------------------------------------------------


_RECONCILE_FIELDS = frozenset({"providerReference", "amountMinor"})


@router.get("/finance/payables")
def list_payables(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMPENSATION_RECONCILE)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.list_payables(conn, workspace_id=workspace_id)}


@router.get("/finance/payables/{payable_id}")
def read_payable(
    workspace_id: str, payable_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMPENSATION_RECONCILE)
    as_identifier(payable_id, what="payable")
    try:
        view = td.read_payable(conn, workspace_id=workspace_id, payable_id=payable_id)
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.post("/finance/payables/{payable_id}/reconcile")
def reconcile_payable(
    workspace_id: str,
    payable_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.COMPENSATION_RECONCILE,
        body=body,
        allowed_fields=_RECONCILE_FIELDS,
    )
    as_identifier(payable_id, what="payable")

    def perform() -> dict[str, Any]:
        try:
            return td.reconcile_payable(
                conn,
                workspace_id=workspace_id,
                payable_id=payable_id,
                provider_reference=body.get("providerReference"),
                amount_minor=body.get("amountMinor"),
            )
        except LookupError:
            raise not_found() from None
        except td.CompensationError as exc:
            raise _refused(exc) from exc

    result = run_idempotently(
        conn,
        context,
        route=f"POST /tester-desk/finance/payables/{payable_id}/reconcile",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.get("/tester/assignments/{assignment_id}/payable")
def tester_payable(
    workspace_id: str, assignment_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    """The owning tester can see their payable's lifecycle state -- never its provider plumbing."""
    _, enrollment = tester_context(conn, request, workspace_id)
    as_identifier(assignment_id, what="assignment")
    try:
        td.assignment_for_tester(
            conn,
            workspace_id=workspace_id,
            assignment_id=assignment_id,
            tester_id=enrollment["testerId"],
        )
    except LookupError:
        raise not_found() from None
    payable = td.payable_for_assignment(
        conn, workspace_id=workspace_id, assignment_id=assignment_id
    )
    response.headers["Cache-Control"] = "no-store"
    if payable is None:
        return {"payable": None}
    return {
        "payable": {
            "payableId": payable["payableId"],
            "amountMinor": payable["amountMinor"],
            "currency": payable["currency"],
            "status": payable["status"],
            "createdAt": payable["createdAt"],
        }
    }


# --- client reports and retention --------------------------------------------------------------


@router.get("/reports/ready")
def reportable_assignments(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_AUTHOR)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.reportable_assignments(conn, workspace_id=workspace_id)}


@router.post("/reports/generate", status_code=status.HTTP_201_CREATED)
def generate_report(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.COMMISSION_AUTHOR,
        body=body,
        allowed_fields=frozenset({"assignmentId"}),
    )
    as_identifier(str(body.get("assignmentId", "")), what="assignment")

    def perform() -> dict[str, Any]:
        try:
            return td.generate_report(
                conn,
                workspace_id=workspace_id,
                assignment_id=str(body["assignmentId"]),
                generated_by=context.principal.user_id,
            )
        except LookupError:
            raise not_found() from None
        except td.ReportError as exc:
            raise _refused(exc) from exc

    result = run_idempotently(
        conn,
        context,
        route="POST /tester-desk/reports/generate",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.get("/client/reports")
def client_reports(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, grants = client_context(conn, request, workspace_id)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.reports_for_client(conn, workspace_id=workspace_id, client_ids=grants)}


@router.get("/client/reports/{report_id}")
def client_report(
    workspace_id: str, report_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    context, _ = client_context(conn, request, workspace_id)
    as_identifier(report_id, what="report")
    try:
        view = td.read_report_for_client(
            conn,
            workspace_id=workspace_id,
            report_id=report_id,
            user_id=context.principal.user_id,
        )
    except LookupError:
        raise not_found() from None
    except td.ReportError:
        # A grant that doesn't cover this report reads exactly like a missing one.
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/client/reports/{report_id}/attachments/{attachment_id}/content")
def client_report_attachment(
    workspace_id: str,
    report_id: str,
    attachment_id: str,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    context, _ = client_context(conn, request, workspace_id)
    as_identifier(report_id, what="report")
    as_identifier(attachment_id, what="attachment")
    try:
        view = td.client_report_attachment(
            conn,
            workspace_id=workspace_id,
            report_id=report_id,
            attachment_id=attachment_id,
            user_id=context.principal.user_id,
        )
    except LookupError:
        raise not_found() from None
    except (td.ReportError, td.EvidenceError) as exc:
        if isinstance(exc, td.ReportError):
            raise not_found() from None
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {
        "attachmentId": view["attachmentId"],
        "kind": view["kind"],
        "contentType": view["contentType"],
        "sha256": view["sha256"],
        "contentBase64": base64.b64encode(view["content"]).decode("ascii"),
    }


@router.post("/retention/purge")
def retention_purge(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.TESTER_DESK_ADMIN,
        body=body,
        allowed_fields=frozenset({"before"}),
    )
    before = body.get("before")
    moment = None
    if before is not None:
        try:
            moment = parse_rfc3339_utc(before, field="before")
        except Exception as exc:
            raise ProblemDetail(ProblemCode.INVALID_INPUT, str(exc)) from exc

    def perform() -> dict[str, Any]:
        return td.retention_purge(conn, workspace_id=workspace_id, before=moment)

    result = run_idempotently(
        conn,
        context,
        route="POST /tester-desk/retention/purge",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


# --- retest and release comparison -------------------------------------------------------------


@router.get("/commissions/{commission_id}/compare")
def compare_commissions(
    workspace_id: str,
    commission_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    against: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_AUTHOR)
    as_identifier(commission_id, what="commission")
    if against is None or not against:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "the 'against' query parameter is required")
    as_identifier(against, what="commission")
    try:
        view = td.compare_commissions(
            conn,
            workspace_id=workspace_id,
            base_commission_id=commission_id,
            candidate_commission_id=against,
        )
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return view


@router.get("/defects/{defect_key}/commissions")
def defect_commissions(
    workspace_id: str, defect_key: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.COMMISSION_AUTHOR)
    response.headers["Cache-Control"] = "no-store"
    return {"items": td.linked_retests(conn, workspace_id=workspace_id, defect_key=defect_key)}
