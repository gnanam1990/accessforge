"""Request-scoped authority, concurrency control and idempotency.

Everything here answers one of three questions, and each has a way of being answered wrongly that
looks reasonable in a diff.

**Who is this, and where?** The session says who, the path says where, and live membership says
what they may do. A `workspaceId` in a body is never consulted: module 03 built
`assert_route_matches_body` for that, and it is applied here so a route author cannot forget it.

**Is this decision about current state?** `If-Match` carries the revision the caller last saw. A
mutation without it is refused rather than applied to whatever state happens to be there now: a
caller that did not read before writing made a decision about nothing.

**Has this already happened?** Idempotency keyed on principal, workspace, route and key, with the
canonical request digest. The same body replays; a different body under the same key is a conflict.

The one subtlety worth stating: a replay **re-checks authorization** before returning the stored
response. Membership revoked between the original call and the retry must take effect, and a cached
response returned without that check is a cached authorization decision.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from fastapi import Request

from accessforge_domain.authorization import AuthorizationError
from accessforge_domain.authorization.principals import HumanPrincipal
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.canonical import digest
from accessforge_persistence import idempotency, rate_limits, workspace_connection

from .auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    MembershipError,
    SessionError,
    assert_route_matches_body,
    require_permission,
    resolve_human_principal,
    resolve_session,
    verify_csrf,
)
from .problems import ProblemCode, ProblemDetail

#: Mutating methods require CSRF protection and, where the resource is revisioned, If-Match.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Bounded page sizes. An unbounded page is a denial-of-service vector and a memory cliff, and the
#: default is small enough that a caller who never paginates still gets a usable answer.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Everything a route needs about who is asking and what they may do."""

    principal: HumanPrincipal
    workspace_id: str
    request_id: str
    idempotency_key: str | None
    if_match: int | None


def _request_id(request: Request) -> str:
    """A correlation id for this request.

    Resolved by the telemetry middleware and read from the scope, so the id in a problem document is
    the same id in the emitted record. Generating one here independently is how the two ended up
    different, which leaves an operator holding a customer's request id that matches nothing.

    Falls back to the header and then to a fresh id for the cases that never pass through the
    middleware -- notably a direct `build_context` call in a test.
    """
    from .telemetry import resolve_request_id

    return resolve_request_id(request)


def _parse_if_match(request: Request) -> int | None:
    raw = request.headers.get("If-Match")
    if raw is None:
        return None
    candidate = raw.strip().strip('"').removeprefix("W/").strip('"')
    if not candidate.isdigit():
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "If-Match must carry the integer revision you last read. A weak or opaque validator "
            "cannot be compared against a revision, so it would be accepted and ignored.",
            request_id=_request_id(request),
        )
    return int(candidate)


class _Denied(Exception):
    """Carries a refusal out of the limiter's transaction so that it rolls back.

    Internal. Leaving the `with` block by exception is what discards the decrements a refused
    request
    made, which is the behaviour the two buckets need: see `enforce_write_rate_limit`.
    """

    def __init__(self, decision: rate_limits.Decision) -> None:
        super().__init__(decision.meaning)
        self.decision = decision


def enforce_write_rate_limit(
    request: Request,
    *,
    principal_id: str,
    workspace_id: str,
    request_id: str,
    now: datetime | None = None,
) -> None:
    """Refuse a mutating request that is arriving faster than the configured rate.

    **On its own connection, committed before the route can fail.** This is the correction: the
    decrements used to run on the request's connection, which `workspace_scope` holds inside a
    single
    transaction for the whole request. Any later refusal -- a permission denial, a stale revision, a
    domain error -- rolled the transaction back, and the tokens came back with it. So precisely the
    requests worth limiting were the ones that were never charged: a caller without permission could
    probe every write route for ever, and a caller whose writes kept failing could retry for ever,
    at
    any rate, while the limiter reported itself working.

    A second connection per mutating request is the cost of that durability, and it is the point
    rather than an oversight: a decision that is only durable when the request succeeds is not a
    limit.

    **Both buckets inside one transaction, so they move together.** A refused request consumes
    nothing: if the workspace bucket denies after the principal bucket was decremented, the
    exception
    leaves the block, the transaction rolls back, and neither token is spent. Otherwise being
    refused
    by one bucket would quietly drain the other, and a caller over their workspace limit would lose
    their personal allowance to refusals they never got any work from.

    **Identity comes from the resolved session and the path, never from the request.** The principal
    is the one `resolve_human_principal` returned from the session cookie and a live membership; the
    workspace is the one in the path that membership was checked against. Nothing here reads a
    header
    or a body field, because a limiter keyed on something the caller chooses is a limiter the caller
    turns off by changing it.

    **Principal first, then workspace, always in that order.** Two requests from one principal in
    different workspaces both take the principal row first and then their own workspace row, so
    there
    is no cycle and no deadlock. The order is load-bearing, not cosmetic.
    """
    if request.method not in MUTATING_METHODS:
        return
    moment = now or datetime.now(UTC)
    config = getattr(request.app.state, "config", None)
    per_principal = int(getattr(config, "rate_limit_principal_per_minute", 120))
    per_workspace = int(getattr(config, "rate_limit_workspace_per_minute", 600))
    burst = float(getattr(config, "rate_limit_burst_multiplier", 1.0))
    database_url = str(getattr(config, "database_url", ""))
    if not database_url:
        raise ProblemDetail(
            ProblemCode.DEPENDENCY_UNAVAILABLE,
            "the write rate limit cannot be evaluated because this process has no configured "
            "database. Refusing rather than admitting an unlimited write: a limiter that fails "
            "open is not a limiter.",
            request_id=request_id,
        )

    try:
        with workspace_connection(database_url, workspace_id) as limiter:
            for scope_kind, scope_id, per_minute in (
                ("PRINCIPAL", principal_id, per_principal),
                ("WORKSPACE", workspace_id, per_workspace),
            ):
                decision = rate_limits.consume(
                    limiter,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    capacity=max(1, int(per_minute * burst)),
                    refill_per_second=per_minute / 60.0,
                    now=moment,
                )
                if not decision.allowed:
                    raise _Denied(decision)
    except _Denied as denied:
        decision = denied.decision
        raise ProblemDetail(
            ProblemCode.RATE_LIMITED,
            f"too many writes: {decision.meaning} Retry-After says when, and it is a whole "
            "number of seconds. This is not a quota -- nothing was consumed and no entitlement "
            "was spent, so the same request succeeds once the rate allows it.",
            extra={
                "scope": decision.scope_kind,
                # The sustained rate, and the burst, as two different numbers. They are the same
                # only when the burst multiplier is one, and reporting the capacity as the
                # per-minute limit told a caller on a 120/minute policy with a burst of two that
                # their limit was 240 -- a rate they cannot sustain and a number nobody set.
                "limitPerMinute": decision.limit_per_minute,
                "burstCapacity": decision.burst_capacity,
                # In the body as well as the header. The header is what intermediaries and SDK
                # retry policies read; the body is what a person reading a log sees.
                "retryAfterSeconds": decision.retry_after_seconds,
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
            request_id=request_id,
        ) from denied


def build_context(
    conn: psycopg.Connection[dict[str, Any]],
    request: Request,
    *,
    workspace_id: str,
    permission: Permission,
    body: dict[str, Any] | None = None,
) -> RequestContext:
    """Resolve authority for one request, or refuse it.

    The order is deliberate and each step is a different refusal: authenticated, then CSRF, then a
    live membership in *this* workspace, then the permission. A caller who is authenticated but
    not a member gets 404 rather than 403 — see `problems.not_found`.
    """
    request_id = _request_id(request)

    try:
        session = resolve_session(conn, session_token=request.cookies.get(SESSION_COOKIE))
    except SessionError as exc:
        raise ProblemDetail(ProblemCode.NOT_AUTHENTICATED, str(exc), request_id=request_id) from exc

    if request.method in MUTATING_METHODS:
        try:
            verify_csrf(session, method=request.method, csrf_token=request.headers.get(CSRF_HEADER))
        except SessionError as exc:
            raise ProblemDetail(ProblemCode.CSRF_REQUIRED, str(exc), request_id=request_id) from exc

    if body is not None:
        try:
            assert_route_matches_body(
                workspace_id_from_route=workspace_id,
                workspace_id_from_body=body.get("workspaceId"),
            )
        except AuthorizationError as exc:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                f"{exc} Authority comes from the authenticated session and the path, never from a "
                "field in the body.",
                request_id=request_id,
            ) from exc

    try:
        principal = resolve_human_principal(
            conn, session=session, workspace_id_from_route=workspace_id
        )
    # MembershipError from the lookup, AuthorizationError from anything the lookup delegates to.
    # Both mean the same thing to a caller with no membership here, and both must produce the same
    # 404: distinguishing them would put a workspace's existence back in the response.
    except (MembershipError, AuthorizationError):
        # Not a 403. A caller with no membership must not be able to tell a workspace that exists
        # from one that does not.
        from .problems import not_found

        problem = not_found()
        problem.request_id = request_id
        raise problem from None

    # After membership, before permission. See `enforce_write_rate_limit` for why that order.
    enforce_write_rate_limit(
        request,
        principal_id=principal.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    )

    try:
        require_permission(principal, permission)
    except AuthorizationError as exc:
        raise ProblemDetail(ProblemCode.PERMISSION_DENIED, str(exc), request_id=request_id) from exc

    return RequestContext(
        principal=principal,
        workspace_id=workspace_id,
        request_id=request_id,
        idempotency_key=request.headers.get("Idempotency-Key"),
        if_match=_parse_if_match(request),
    )


def require_if_match(context: RequestContext) -> int:
    """A revisioned mutation needs the revision the caller last read."""
    if context.if_match is None:
        raise ProblemDetail(
            ProblemCode.IF_MATCH_REQUIRED,
            "this mutation changes a revisioned resource and requires an If-Match header carrying "
            "the revision you last read. Without it the change would be applied to whatever state "
            "happens to be current, and the decision behind it was about a state you saw earlier.",
            request_id=context.request_id,
        )
    return context.if_match


@dataclass(frozen=True, slots=True)
class IdempotentOutcome:
    replayed: bool
    response: dict[str, Any] | None


def run_idempotently(
    conn: psycopg.Connection[dict[str, Any]],
    context: RequestContext,
    *,
    route: str,
    body: dict[str, Any],
    perform: Callable[[], dict[str, Any]],
) -> IdempotentOutcome:
    """Perform a mutation once, replaying the stored response for a repeated key.

    Without a key the operation simply runs: idempotency is a client's tool for making a retry safe,
    not a requirement the server imposes on every caller.

    A replay returns the recorded response *after* the caller's authority has already been resolved
    by `build_context`. That ordering is the point: membership revoked between the original call
    and the retry takes effect, because a stored response returned without re-checking is a stored
    authorization decision.
    """
    if context.idempotency_key is None:
        return IdempotentOutcome(replayed=False, response=perform())

    request_digest = digest(body)
    try:
        reservation = idempotency.reserve(
            conn,
            workspace_id=context.workspace_id,
            principal_id=context.principal.user_id,
            route=route,
            idempotency_key=context.idempotency_key,
            request_digest=request_digest,
        )
    except idempotency.IdempotencyConflict as exc:
        raise ProblemDetail(
            ProblemCode.IDEMPOTENCY_KEY_REUSED,
            f"{exc} The same key with a different body is two different operations wearing one "
            "name, and replaying the first would silently discard the second.",
            request_id=context.request_id,
        ) from exc

    if reservation.is_replay:
        return IdempotentOutcome(replayed=True, response=reservation.result)

    result = perform()
    idempotency.complete(conn, operation_id=reservation.operation_id, result=result)
    return IdempotentOutcome(replayed=False, response=result)


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a stable, cursor-ordered listing."""

    items: list[dict[str, Any]]
    next_cursor: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"items": self.items, "nextCursor": self.next_cursor}


def clamp_page_size(requested: int | None) -> int:
    """Bound the page size, refusing a nonsense value rather than silently correcting it.

    Silently clamping 100000 to 200 would leave a caller believing they had the whole list. Refusing
    tells them their assumption was wrong, which is the thing they need to know.
    """
    if requested is None:
        return DEFAULT_PAGE_SIZE
    if requested < 1 or requested > MAX_PAGE_SIZE:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"page size must be between 1 and {MAX_PAGE_SIZE}. A larger value is refused rather "
            "than clamped: silently returning fewer items than asked for leaves a caller believing "
            "they hold the whole list.",
        )
    return requested


def scoped_connection(database_url: str, workspace_id: str) -> Iterator[Any]:
    """A connection scoped to the workspace in the path, for the life of the request.

    Every query a route makes is therefore workspace-scoped at the database, so a route that forgets
    a `WHERE workspace_id = …` returns nothing rather than another tenant's rows.
    """
    with workspace_connection(database_url, workspace_id) as conn:
        yield conn
