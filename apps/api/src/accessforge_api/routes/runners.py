"""Runner enrollment, preflight, status and reset."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, status

from accessforge_api.dependencies import clamp_page_size, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import (
    as_body,
    as_identifier,
    authorize,
    workspace_scope,
)
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.runners import PhysicalSession, RunnerProfile
from accessforge_domain.runners.identity import EnrollmentError
from accessforge_domain.runners.preflight import PreflightCheck, PreflightResult
from accessforge_domain.states import Condition
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


@router.get("/runners")
def list_runners(
    workspace_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The runner inventory, with the evidence behind each status.

    `preflightPassedAt` is the field that matters, and it is separate from `status` on purpose. A
    runner process being reachable is not proof that a screen reader is running on it, that the
    reader is the one enrolled, or that anything was ever read back from a real desktop. INV-02
    turns on that distinction: a missing reader capability must never resolve to a pass, and a UI
    that inferred readiness from "the row exists" would be the first place that inference is made.

    The reset counter is included because a desktop that keeps needing resets is a desktop nobody
    should be scheduling work onto, and that pattern is invisible from a single status word.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    rows = conn.execute(
        """
        SELECT r.id, r.name, r.status, r.platform, r.profile, r.lease_epoch,
               r.quarantine_reason, r.revoked_at, r.created_at,
               (SELECT max(p.recorded_at) FROM runner_preflight p
                 WHERE p.runner_id = r.id AND p.successful) AS preflight_passed_at,
               (SELECT count(*) FROM runner_reset x WHERE x.runner_id = r.id) AS reset_count,
               (SELECT count(*) FROM desktop_lease l
                 WHERE l.runner_id = r.id AND l.released_at IS NULL) AS active_leases
          FROM runner r
         WHERE (%s::uuid IS NULL OR r.id > %s::uuid)
         ORDER BY r.id
         LIMIT %s
        """,
        (after, after, size + 1),
    ).fetchall()

    items = [
        {
            "runnerId": str(r["id"]),
            "name": str(r["name"]),
            "status": str(r["status"]),
            "platform": str(r["platform"]),
            # The enrolled profile as recorded: reader name, reader version, browser, and whatever
            # else enrollment captured. Reported verbatim rather than summarised, because a version
            # difference is the whole reason a matched runner may still be the wrong one.
            "profile": dict(r["profile"]),
            "leaseEpoch": int(r["lease_epoch"]),
            "quarantineReason": r["quarantine_reason"],
            "revoked": r["revoked_at"] is not None,
            # None means no preflight has ever passed on this runner. Not `false`, and not omitted:
            # "never proved" and "proved a while ago" are different states and an operator acts on
            # them differently.
            "preflightPassedAt": (
                None if r["preflight_passed_at"] is None else str(r["preflight_passed_at"])
            ),
            "resetCount": int(r["reset_count"]),
            "hasActiveLease": int(r["active_leases"]) > 0,
            "createdAt": str(r["created_at"]),
        }
        for r in rows[:size]
    ]
    return {
        "items": items,
        "nextCursor": items[-1]["runnerId"] if len(rows) > size else None,
        "readinessMeaning": (
            "READY means this runner passed a preflight that read something back from a real "
            "desktop. It is not inferred from the runner process being reachable, and no status "
            "here is evidence that a journey will pass."
        ),
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


PREFLIGHT_FIELDS = frozenset(
    {
        "runnerProfileDigest",
        "environmentConfigDigest",
        "manifestDigest",
        "observedReaderVersion",
        "observedBrowserVersion",
        "observedLocale",
        "observedKeyboardLayout",
        "desktopSessionKey",
        "observedAt",
        "checks",
    }
)


@router.post("/runners/{runner_id}/preflights", status_code=status.HTTP_201_CREATED)
def submit_preflight(
    workspace_id: str,
    runner_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Submit a preflight, and let the server decide what it established.

    Three things this route will not do, each of which is the obvious convenience:

    **It does not accept a `successful` field.** Success is computed from the submitted checks. A
    runner that could declare itself ready would be the only witness to its own readiness, and the
    whole purpose of a preflight is that READY means something a server checked.

    **An absent check is not a passing check.** Coverage is compared for equality against the
    required set, so a supervisor that stops reporting one after an upgrade fails preflight rather
    than quietly dropping the guarantee. `UNKNOWN` is available and is not a pass: a runner that
    could not determine whether the screen was locked has not shown that it was unlocked.

    **It does not trust the runner's own version-match check.** A supervisor that lied about its
    reader version would also lie about whether that version matches its profile, so the server
    compares the observed values against the enrolled profile itself.

    A failed preflight is a 201, not a 4xx. The submission was accepted and recorded; what it
    established is in the body. Answering 400 would conflate "you sent something malformed" with
    "your desktop is not ready", and the second is a result worth keeping.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.RUN_REQUEST,
        body=body,
        allowed_fields=PREFLIGHT_FIELDS,
    )

    missing = sorted(PREFLIGHT_FIELDS - set(body))
    if missing:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"missing required field(s): {', '.join(missing)}",
            request_id=context.request_id,
        )

    checks = body["checks"]
    if not isinstance(checks, dict):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "checks must be an object mapping each check name to TRUE, FALSE or UNKNOWN",
            request_id=context.request_id,
        )

    try:
        parsed = {
            PreflightCheck(str(name)): Condition(str(value)) for name, value in checks.items()
        }
    except ValueError as exc:
        # A closed vocabulary on both sides. If a runner could name its own checks, the
        # required-coverage comparison would silently stop covering anything.
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"unrecognised check name or condition: {exc}. Both vocabularies are closed, because a "
            "runner that could invent a check name could satisfy coverage without performing one.",
            request_id=context.request_id,
        ) from exc

    def perform() -> dict[str, Any]:
        try:
            result = PreflightResult(
                runner_profile_digest=str(body["runnerProfileDigest"]),
                environment_config_digest=str(body["environmentConfigDigest"]),
                manifest_digest=str(body["manifestDigest"]),
                observed_reader_version=str(body["observedReaderVersion"]),
                observed_browser_version=str(body["observedBrowserVersion"]),
                observed_locale=str(body["observedLocale"]),
                observed_keyboard_layout=str(body["observedKeyboardLayout"]),
                desktop_session_key=str(body["desktopSessionKey"]),
                observed_at=str(body["observedAt"]),
                checks=parsed,
            )
        except (ValueError, TypeError) as exc:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
            ) from exc

        try:
            record = runners.record_preflight(
                conn,
                workspace_id=workspace_id,
                runner_id=as_identifier(runner_id, what="runnerId"),
                result=result,
            )
        except runners.RunnerError as exc:
            # "no such runner in this workspace" is the uniform 404; anything else is a refusal
            # about the submission itself.
            if "no such runner" in str(exc):
                raise not_found() from exc
            raise ProblemDetail(
                ProblemCode.CONFLICT, str(exc), request_id=context.request_id
            ) from exc

        return {
            "preflightId": record.preflight_id,
            "successful": record.successful,
            "runnerStatus": str(record.runner_status),
            "refusalSummary": record.refusal_summary,
            "meaning": (
                "successful is computed from the checks you submitted, not taken from your "
                "submission. READY means this server compared the observed reader and browser "
                "versions against the enrolled profile and found every required check TRUE -- not "
                "that the runner reported itself ready. A preflight is evidence for the profile, "
                "environment and manifest it names and for nothing else."
            ),
        }

    outcome = run_idempotently(
        conn, context, route="POST /runners/preflights", body=body, perform=perform
    )
    return outcome.response or {}
