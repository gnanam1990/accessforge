"""RFC7807 problem details, and the rules about what a problem may say.

An error response is read by two audiences with opposite needs. A developer wants to know what went
wrong; an attacker wants to know what exists. Those conflict precisely where it matters most — a
404 that distinguishes "no such run" from "that run is in another workspace" is a cross-tenant
existence oracle, and it is the most natural thing in the world to write.

So three rules are enforced here rather than remembered:

**One code per condition, from a closed set.** A caller can branch on `code`; a caller cannot branch
on prose, and prose changes. `ProblemCode` is the contract.

**Cross-tenant and unknown are the same answer.** `RESOURCE_NOT_FOUND` carries no identifier and no
hint about which of the two applies. A workspace cannot learn what another workspace holds by
watching which errors it gets.

**No stack traces, no secrets, no internal identifiers.** `detail` is written by hand for each
condition. There is no path by which an exception message reaches a response body, because the
exception messages in this codebase deliberately contain the specifics that help an operator — and
an operator is not an anonymous caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse

#: The media type RFC7807 defines. Set explicitly, because a problem document served as
#: `application/json` is a problem document a client library will not recognise as one.
PROBLEM_CONTENT_TYPE = "application/problem+json"


class ProblemCode(StrEnum):
    """The closed set of machine-readable error conditions.

    Closed because a caller branches on these. A free-form code would mean every caller either
    matches on prose or gives up and treats all errors alike, and the second is what actually
    happens.
    """

    INVALID_INPUT = "INVALID_INPUT"
    UNEXPECTED_FIELD = "UNEXPECTED_FIELD"
    MALFORMED_DIGEST = "MALFORMED_DIGEST"
    AMBIGUOUS_TIMESTAMP = "AMBIGUOUS_TIMESTAMP"

    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    CSRF_REQUIRED = "CSRF_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    """Also returned for a resource in another workspace. Deliberately indistinguishable: a 403 for
    a cross-tenant resource tells the caller it exists."""

    STALE_REVISION = "STALE_REVISION"
    IF_MATCH_REQUIRED = "IF_MATCH_REQUIRED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    CONFLICT = "CONFLICT"

    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    """422. The request is well-formed and the system cannot do it -- an unsupported reader profile,
    a journey requiring a capability no runner has."""

    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


#: The status each code maps to. One place, so a code cannot acquire two meanings by being raised
#: with different statuses in different routes.
_STATUS: dict[ProblemCode, int] = {
    ProblemCode.INVALID_INPUT: status.HTTP_400_BAD_REQUEST,
    ProblemCode.UNEXPECTED_FIELD: status.HTTP_400_BAD_REQUEST,
    ProblemCode.MALFORMED_DIGEST: status.HTTP_400_BAD_REQUEST,
    ProblemCode.AMBIGUOUS_TIMESTAMP: status.HTTP_400_BAD_REQUEST,
    ProblemCode.NOT_AUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    ProblemCode.CSRF_REQUIRED: status.HTTP_403_FORBIDDEN,
    ProblemCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
    ProblemCode.RESOURCE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ProblemCode.STALE_REVISION: status.HTTP_409_CONFLICT,
    ProblemCode.IF_MATCH_REQUIRED: status.HTTP_428_PRECONDITION_REQUIRED,
    ProblemCode.IDEMPOTENCY_KEY_REUSED: status.HTTP_409_CONFLICT,
    ProblemCode.CONFLICT: status.HTTP_409_CONFLICT,
    # UNPROCESSABLE_CONTENT is the current name for 422; the old alias is deprecated.
    ProblemCode.UNSUPPORTED_CAPABILITY: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ProblemCode.QUOTA_EXHAUSTED: status.HTTP_429_TOO_MANY_REQUESTS,
    ProblemCode.DEPENDENCY_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


class ProblemDetail(Exception):
    """A refusal, carrying exactly what is safe to tell an anonymous caller.

    An exception rather than a return value so a route can refuse from anywhere in its body without
    every intermediate function needing a union return type. The handler turns it into a response.
    """

    def __init__(
        self,
        code: ProblemCode,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = _STATUS[code]
        self.request_id = request_id or str(uuid.uuid4())
        # Bounded to scalars. A nested object here is where a row, an internal identifier or a
        # message from another tenant eventually travels: the shallow shape makes that visible in a
        # diff rather than arriving inside a dict someone passed through.
        self.extra = {
            k: v for k, v in (extra or {}).items() if isinstance(v, str | int | bool | float)
        }

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            media_type=PROBLEM_CONTENT_TYPE,
            content={
                "type": f"https://accessforge.invalid/problems/{self.code.lower()}",
                "title": _TITLES[self.code],
                "status": self.status_code,
                "code": str(self.code),
                "detail": self.detail,
                "requestId": self.request_id,
                **self.extra,
            },
        )


_TITLES: dict[ProblemCode, str] = {
    ProblemCode.INVALID_INPUT: "Invalid input",
    ProblemCode.UNEXPECTED_FIELD: "Unexpected field",
    ProblemCode.MALFORMED_DIGEST: "Malformed digest",
    ProblemCode.AMBIGUOUS_TIMESTAMP: "Ambiguous timestamp",
    ProblemCode.NOT_AUTHENTICATED: "Not authenticated",
    ProblemCode.CSRF_REQUIRED: "CSRF token required",
    ProblemCode.PERMISSION_DENIED: "Permission denied",
    ProblemCode.RESOURCE_NOT_FOUND: "Not found",
    ProblemCode.STALE_REVISION: "Stale revision",
    ProblemCode.IF_MATCH_REQUIRED: "If-Match required",
    ProblemCode.IDEMPOTENCY_KEY_REUSED: "Idempotency key reused with a different body",
    ProblemCode.CONFLICT: "Conflict",
    ProblemCode.UNSUPPORTED_CAPABILITY: "Unsupported capability",
    ProblemCode.QUOTA_EXHAUSTED: "Quota exhausted",
    ProblemCode.DEPENDENCY_UNAVAILABLE: "Dependency unavailable",
}


def not_found() -> ProblemDetail:
    """The one answer for both "does not exist" and "belongs to another workspace".

    A helper rather than a constructed detail at each call site, so the two cases cannot drift into
    distinguishable messages. The detail is deliberately uninformative — an anonymous caller learns
    nothing, and an operator has the server logs.
    """
    return ProblemDetail(
        ProblemCode.RESOURCE_NOT_FOUND,
        "no such resource is available to you. This is the same answer for a resource that does "
        "not exist and one that belongs to another workspace: distinguishing them would let a "
        "caller discover what other tenants hold.",
    )


@dataclass(frozen=True, slots=True)
class SafeFieldPolicy:
    """Fields a route accepts, so anything else is refused rather than ignored.

    The module prompt asks for unexpected *security-sensitive* fields to be rejected. This refuses
    every unexpected field, because the distinction cannot be drawn at the boundary: `workspaceId`
    in a body looks exactly like a harmless echo until the day something reads it.
    """

    allowed: frozenset[str]

    def assert_no_unexpected(self, body: dict[str, Any]) -> None:
        unexpected = sorted(set(body) - self.allowed)
        if unexpected:
            raise ProblemDetail(
                ProblemCode.UNEXPECTED_FIELD,
                f"the request body contains fields this route does not accept: "
                f"{', '.join(unexpected)}. Unexpected fields are refused rather than ignored: an "
                "ignored field is one a later version might start reading, and a body that "
                "silently carried `workspaceId` would be one that eventually got believed.",
                extra={"unexpectedFieldCount": len(unexpected)},
            )
