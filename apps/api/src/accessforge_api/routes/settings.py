"""Usage, entitlements and retention policy.

Three read-and-configure surfaces, and one rule shared by all of them: **what is served is the
authoritative record, never a summary computed for a dashboard.** A usage figure assembled from a
cached constant is a number an operator will act on and nobody can reconcile.

**Nothing here moves money.** There is no price, no currency, no payment instrument and no computed
saving in any response. R1's scope is measured usage and an administrator-managed limit; an endpoint
that reported a cost would be the first half of a billing system nobody designed.

**Configuring is a revisioned append, and it requires the revision the caller last read.** Two
administrators raising a limit at the same moment is exactly when a silent last-writer-wins would
discard somebody's decision without telling either of them.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import require_if_match
from accessforge_api.problems import ProblemCode, ProblemDetail
from accessforge_api.routes._common import as_body, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_persistence import budgets, retention

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["settings"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]


@router.get("/usage")
def read_usage(
    workspace_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Measured consumption against the configured limit.

    Every number is read from `usage_event`. `measured`, `estimated` and `unavailableEvents` are
    reported separately because they are different kinds of claim: something this system counted,
    something a provider reported about itself, and something that could not be obtained. A single
    total would make the third look like zero.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    try:
        entitlement = budgets.current_entitlement(conn, workspace_id=workspace_id)
    except budgets.NoEntitlement as exc:
        raise ProblemDetail(
            ProblemCode.DEPENDENCY_UNAVAILABLE,
            f"{exc} An administrator configures it; nothing raises it automatically.",
        ) from exc

    totals = budgets.usage_since(conn, workspace_id=workspace_id, entitlement=entitlement)
    response.headers["ETag"] = f'"{entitlement.revision}"'
    return {
        "entitlementRevision": entitlement.revision,
        "configuredBy": entitlement.configured_by,
        "reason": entitlement.reason,
        "window": "rolling 24 hours",
        "usage": [
            {
                "kind": total.kind,
                "measured": total.measured,
                "estimated": total.estimated,
                # A count of events, not a quantity of usage.
                "unavailableEvents": total.unavailable_events,
                "countedAgainstLimit": total.counted_against_limit,
                "limit": total.limit,
                "remaining": max(0, total.limit - total.counted_against_limit),
            }
            for total in totals
        ],
        "concurrentRuns": budgets.concurrent_runs(conn, workspace_id=workspace_id),
        "maxConcurrentRuns": entitlement.max_concurrent_runs,
        "meaning": (
            "Measured is what this system counted. Estimated is what a provider reported about its "
            "own consumption, and it counts against the limit because excluding it would leave "
            "anything self-reported unbounded. Unavailable is a count of events whose quantity "
            "could not be obtained; it is not zero usage. No figure here is a cost, and nothing "
            "here charges anyone."
        ),
    }


@router.get("/settings/entitlement")
def read_entitlement(
    workspace_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    try:
        entitlement = budgets.current_entitlement(conn, workspace_id=workspace_id)
    except budgets.NoEntitlement as exc:
        raise ProblemDetail(ProblemCode.DEPENDENCY_UNAVAILABLE, str(exc)) from exc
    response.headers["ETag"] = f'"{entitlement.revision}"'
    return {
        "revision": entitlement.revision,
        "maxRunsPerDay": entitlement.max_runs_per_day,
        "maxActionsPerDay": entitlement.max_actions_per_day,
        "maxWallSecondsPerDay": entitlement.max_wall_seconds_per_day,
        "maxModelTokensPerDay": entitlement.max_model_tokens_per_day,
        "maxConcurrentRuns": entitlement.max_concurrent_runs,
        "configuredBy": entitlement.configured_by,
        "reason": entitlement.reason,
    }


@router.put("/settings/entitlement", status_code=status.HTTP_201_CREATED)
def configure_entitlement(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Append a new entitlement revision.

    `If-Match` carries the revision the caller last read, and it is required. Without it a caller
    is replacing a limit they may not have seen — and the only reason to change a limit is a
    decision about the one currently in force.

    There is no route that *lowers* usage, and none that grants an exception to a single run. An
    exception granted per run would be a limit with a bypass, and the bypass is the thing an
    attacker looks for.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.WORKSPACE_CONFIGURE,
        body,
        frozenset(
            {
                "maxRunsPerDay",
                "maxActionsPerDay",
                "maxWallSecondsPerDay",
                "maxModelTokensPerDay",
                "maxConcurrentRuns",
                "reason",
            }
        ),
    )
    expected = require_if_match(context)

    def whole(key: str) -> int:
        value = body.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                f"{key} must be a whole number of zero or more. There is no value meaning "
                "unlimited: an unbounded allowance is one nobody decided on.",
                extra={"field": key},
                request_id=context.request_id,
            )
        return value

    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "say why this limit is what it is. A limit with no recorded reason is one nobody can "
            "be asked about later.",
            extra={"field": "reason"},
            request_id=context.request_id,
        )

    try:
        revision = budgets.configure_entitlement(
            conn,
            workspace_id=workspace_id,
            max_runs_per_day=whole("maxRunsPerDay"),
            max_actions_per_day=whole("maxActionsPerDay"),
            max_wall_seconds_per_day=whole("maxWallSecondsPerDay"),
            max_model_tokens_per_day=whole("maxModelTokensPerDay"),
            max_concurrent_runs=whole("maxConcurrentRuns"),
            configured_by=context.principal.user_id,
            reason=reason.strip(),
            expected_revision=expected,
        )
    except budgets.BudgetError as exc:
        raise ProblemDetail(
            ProblemCode.STALE_REVISION, str(exc), request_id=context.request_id
        ) from exc

    return {
        "revision": revision,
        "meaning": (
            "A new revision. The previous one is unchanged, so an admission decided under it "
            "remains explainable."
        ),
    }


@router.get("/settings/retention")
def read_retention(
    workspace_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """The retention class table, with what deleting under each class costs.

    `invalidatesCompleteness` is on every row because it is the consequence nobody expects:
    deleting evidence does not merely free space, it makes any completeness claim that depended on
    that evidence untrue — and an export already downloaded still contains what was deleted.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    policy = retention.current_policy(conn, workspace_id=workspace_id)
    response.headers["ETag"] = f'"{policy.revision}"'
    return {
        "revision": policy.revision,
        "classes": [
            {
                "evidenceClass": entry.evidence_class,
                "retainDays": entry.retain_days,
                "consentRequired": entry.consent_required,
                "invalidatesCompleteness": entry.invalidates_completeness,
                "meaning": entry.meaning,
            }
            for entry in policy.entries
        ],
        "limits": [
            "Deleting evidence invalidates any completeness claim that depended on it. The runs "
            "concerned do not become inconclusive retroactively; their evidence becomes unable to "
            "support what it supported.",
            "An export already downloaded still contains what it contained. Nothing here reaches a "
            "copy somebody else holds.",
            "Backups expire on their own schedule, and a restore from one predates this policy.",
        ],
    }


@router.put("/settings/retention", status_code=status.HTTP_201_CREATED)
def configure_retention(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.WORKSPACE_CONFIGURE,
        body,
        frozenset({"classes"}),
    )
    expected = require_if_match(context)

    entries = body.get("classes")
    if not isinstance(entries, list) or not entries:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "classes must list every evidence class and its retention",
            extra={"field": "classes"},
            request_id=context.request_id,
        )

    try:
        revision = retention.configure_policy(
            conn,
            workspace_id=workspace_id,
            entries=entries,
            configured_by=context.principal.user_id,
            expected_revision=expected,
        )
    except retention.RetentionError as exc:
        code = (
            ProblemCode.STALE_REVISION if "read revision" in str(exc) else ProblemCode.INVALID_INPUT
        )
        raise ProblemDetail(code, str(exc), request_id=context.request_id) from exc

    return {"revision": revision}
