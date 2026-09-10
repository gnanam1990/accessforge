"""Runner enrollment, preflight, status and reset."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, status

from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.runners import PhysicalSession, RunnerProfile
from accessforge_domain.runners.identity import EnrollmentError
from accessforge_persistence import runners

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["runners"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]


@router.post("/runners/enrollment-tokens", status_code=status.HTTP_201_CREATED)
def issue_enrollment_token(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Issue a short-lived single-use enrollment credential.

    The token is returned exactly once, here. There is no route that reads one back: an endpoint
    that could reveal an enrollment token would be a way to take over a tenant's desktop, and
    "only administrators can call it" is a weaker property than "it does not exist".
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.INFRASTRUCTURE_OPERATE,
        body,
        frozenset({"ttlSeconds"}),
    )
    try:
        token = runners.issue_enrollment_token(
            conn,
            workspace_id=workspace_id,
            created_by=context.principal.user_id,
            ttl_seconds=int(body.get("ttlSeconds", runners.DEFAULT_ENROLLMENT_TOKEN_SECONDS)),
        )
    except runners.RunnerError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
        ) from exc
    return {"tokenId": token.token_id, "token": token.token, "expiresAt": token.expires_at}


@router.post("/runners", status_code=status.HTTP_201_CREATED)
def enroll_runner(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.INFRASTRUCTURE_OPERATE,
        body,
        frozenset({"token", "name", "session", "profile"}),
    )
    try:
        session_body = body["session"]
        profile_body = body["profile"]
        enrolled = runners.enroll_runner(
            conn,
            workspace_id=workspace_id,
            token=str(body["token"]),
            name=str(body["name"]),
            session=PhysicalSession(
                device_id=str(session_body["deviceId"]),
                platform=str(session_body["platform"]),
                interactive_session_id=str(session_body["interactiveSessionId"]),
                console=bool(session_body["console"]),
            ),
            profile=RunnerProfile(
                platform=str(profile_body["platform"]),
                reader_name=str(profile_body["readerName"]),
                reader_version=str(profile_body["readerVersion"]),
                browser_name=str(profile_body["browserName"]),
                browser_version=str(profile_body["browserVersion"]),
                locale=str(profile_body["locale"]),
                keyboard_layout=str(profile_body["keyboardLayout"]),
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "session and profile must each carry their full set of fields; a partially described "
            "desktop is one whose identity cannot be compared with another's",
            request_id=context.request_id,
        ) from exc
    except EnrollmentError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
        ) from exc
    except runners.RunnerError as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc), request_id=context.request_id) from exc

    # PREFLIGHT_REQUIRED, always. There is no request body that could produce a READY runner.
    return {
        "runnerId": enrolled.runner_id,
        "status": str(enrolled.status),
        "profileDigest": enrolled.profile_digest,
    }


@router.get("/runners/{runner_id}")
def inspect_runner(
    workspace_id: str, runner_id: str, request: Request, conn: Conn
) -> dict[str, Any]:
    """Everything an operator needs before deciding whether a reset is safe.

    Includes the unresolved actions and the local-impact warning, because "is anything still in
    flight" is what separates a reset that is an inconvenience from one that interrupts work.
    """
    authorize(conn, request, workspace_id, Permission.INFRASTRUCTURE_OPERATE)
    try:
        report = runners.inspect_runner(conn, runner_id=runner_id)
    except runners.RunnerError as exc:
        raise not_found() from exc

    runner = report["runner"]
    return {
        "runnerId": str(runner["id"]),
        "name": str(runner["name"]),
        "status": str(runner["status"]),
        "platform": str(runner["platform"]),
        "leaseEpoch": int(runner["lease_epoch"]),
        "quarantineReason": runner["quarantine_reason"],
        "activeLease": (
            None
            if report["activeLease"] is None
            else {
                "leaseId": str(report["activeLease"]["id"]),
                "runId": str(report["activeLease"]["run_id"]),
                "epoch": int(report["activeLease"]["epoch"]),
                "deadlineAt": str(report["activeLease"]["deadline_at"]),
                "cancelRequestedAt": (
                    None
                    if report["activeLease"]["cancel_requested_at"] is None
                    else str(report["activeLease"]["cancel_requested_at"])
                ),
            }
        ),
        "unresolvedActions": [
            {"actionId": str(a["id"]), "action": str(a["action"])}
            for a in report["unresolvedActions"]
        ],
        "ambiguousActions": [
            {"actionId": str(a["id"]), "reason": str(a["ambiguity_reason"])}
            for a in report["ambiguousActions"]
        ],
        "localImpactWarning": report["localImpactWarning"],
    }


@router.post("/runners/{runner_id}/resets", status_code=status.HTTP_201_CREATED)
def reset_runner(
    workspace_id: str, runner_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Attempt the trusted reset that is the only route out of quarantine.

    `succeeded` comes from the reset procedure, never from the runner. A failed reset leaves the
    desktop quarantined: failing to prove the old actor cannot act is not proof that it cannot.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.INFRASTRUCTURE_OPERATE,
        body,
        frozenset({"succeeded", "proof"}),
    )
    try:
        outcome = runners.reset_runner(
            conn,
            workspace_id=workspace_id,
            runner_id=runner_id,
            requested_by=context.principal.user_id,
            succeeded=bool(body["succeeded"]),
            proof=dict(body.get("proof") or {}),
        )
    except KeyError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "succeeded is required and must come from the reset procedure rather than be assumed",
            request_id=context.request_id,
        ) from exc
    except runners.RunnerError as exc:
        raise not_found() from exc

    return {
        "resetId": outcome.reset_id,
        "succeeded": outcome.succeeded,
        "runnerStatus": str(outcome.runner_status),
        "fencedEpoch": outcome.fenced_epoch,
        "localImpactWarning": runners.RESET_LOCAL_IMPACT_WARNING,
    }
