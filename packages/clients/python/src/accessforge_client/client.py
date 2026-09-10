"""Talking to the API safely, which is the part a contract cannot describe.

Written by hand for that reason. An OpenAPI document says a route exists and what it returns; it
does not say that a 202 is a request rather than a result, that reusing an `Idempotency-Key` with a
different body is two operations wearing one name, or that a 404 on a resource in another workspace
is deliberately indistinguishable from one that does not exist. A generated client would get all
three wrong confidently.

Four decisions carry the safety here:

**A 202 is a `Requested`, not a return value.** The API answers 202 for anything it has recorded
rather than done, and a client that returned the body as though it were an outcome would let a
caller write `if client.request_run(...)` and believe a run had happened. `Requested` has no
truthiness anybody would misread and carries the operation id to poll.

**A problem document is a typed exception carrying its code.** Callers branch on `code`; nobody can
usefully branch on prose, and the prose changes. `ApiProblem.code` is the contract.

**Nothing retries on its own.** A retried mutation without an `Idempotency-Key` is a second
operation. The client makes retrying *possible* — pass the key — and refuses to make it automatic,
because a library that silently retried a run request would spend somebody's budget twice.

**CSRF is paired with the session and not stored separately.** Both come from signing in, and the
client sends the header on every mutation. A caller cannot forget it, which is the point: a
forgotten CSRF header is a 403 in production and a puzzle in a log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ._operations import OPERATIONS, Operation

#: The header the API reads for CSRF. Named here rather than passed in, because a caller who could
#: choose it could choose wrongly and would find out in production.
CSRF_HEADER = "x-csrf-token"
SESSION_COOKIE = "accessforge_session"

#: The CSRF token arrives as a cookie and is **never** in the sign-in response body. That is
#: deliberate on the server's side: a body copy reaches the browser console, the network panel of a
#: shared screen, and any log that records a response. A client that read it from the body would
#: work only against a server that made that mistake.
CSRF_COOKIE = "accessforge_csrf"

DEFAULT_TIMEOUT = 30.0


class ApiProblem(Exception):
    """An RFC7807 refusal, carrying the code a caller should branch on."""

    def __init__(self, *, status: int, code: str, detail: str, request_id: str) -> None:
        super().__init__(f"{code} ({status}): {detail}")
        self.status = status
        self.code = code
        self.detail = detail
        self.request_id = request_id
        """The only identifier in the response body. Quote it when reporting a problem: it is what
        connects what you saw to what the server logged."""


class NotAuthenticated(ApiProblem):
    """No usable session. Distinct because the remedy is to sign in, not to change the request."""


class StaleRevision(ApiProblem):
    """The resource moved since you read it, so your decision was about state that is gone.

    Distinct from a generic conflict because the remedy is specific and mechanical: re-read, decide
    again, resend with the new revision. A caller that retried the same body with the same
    `If-Match` would loop forever.
    """


#: Fields a 202 body may use to name the thing it recorded, in the order they are looked for. Not a
#: single field name, because the routes that answer 202 name their subject differently and
#: hard-coding one would make `identifier` silently empty for the others — which is exactly how a
#: caller ends up with a request they cannot poll.
_IDENTITY_FIELDS = ("operationId", "runId", "exportId", "id")


@dataclass(frozen=True, slots=True)
class Requested:
    """A 202. Something was recorded, and nothing has happened yet.

    Deliberately not a boolean and deliberately not merged into the success path. A run that has
    been requested is not a run that has started, let alone one that passed, and every convenience
    this class could offer would make that easier to forget.
    """

    status_location: str | None
    """Where to look for what actually happened. The authoritative answer to "and then what",
    which is why it is a field rather than something a caller assembles from an id."""

    body: dict[str, Any]

    @property
    def identifier(self) -> str:
        """What the server called the thing it recorded, or "" if it named nothing."""
        for field in _IDENTITY_FIELDS:
            value = self.body.get(field)
            if value:
                return str(value)
        return ""

    @property
    def means(self) -> str:
        return (
            "The server recorded this request. It has not been performed. Poll the status location "
            "for what actually happened; nothing here is a result."
        )


@dataclass(frozen=True, slots=True)
class Session:
    """A signed-in session. The CSRF token travels with it, because it is useless apart from it."""

    session_token: str
    csrf_token: str
    user_id: str


class AccessForgeClient:
    """A client that cannot bypass server policy, and does not try to.

    Every check this API performs — membership, permission, revision, quota, idempotency — happens
    on the server. Nothing here validates on the client's behalf and then skips the round trip: a
    client-side check that disagreed with the server would be a second, wrong, definition of the
    rules, and the wrong one is always the one somebody trusts.
    """

    def __init__(
        self,
        base_url: str,
        *,
        session: Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._session = session
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        if session is not None:
            self._http.cookies.set(SESSION_COOKIE, session.session_token)

    def __enter__(self) -> AccessForgeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # --- authentication --------------------------------------------------------------------------

    def sign_in(self, email: str) -> Session:
        """Sign in against a deployment configured for it.

        A deployment with `ACCESSFORGE_IDENTITY_PROVIDER=none` answers 503 here and names the
        missing dependency, which is the honest answer: it cannot sign anyone in. The client passes
        that through rather than inventing a session.
        """
        response = self._http.post("/v1/sessions", json={"email": email})
        body = self._body(response)

        token = self._http.cookies.get(SESSION_COOKIE)
        csrf = self._http.cookies.get(CSRF_COOKIE)
        if not token or not csrf:
            # The API answered successfully and set no session cookie. Refused rather than stored as
            # an empty token, which would produce a client that looks signed in and gets 401 on
            # every call — a failure that surfaces one request later, somewhere else.
            raise ApiProblem(
                status=response.status_code,
                code="NOT_AUTHENTICATED",
                detail=(
                    "the server accepted the sign-in and did not set both the session and CSRF "
                    "cookies. Nothing here could authenticate a later mutation, so no session was "
                    "stored: a client that looked signed in and failed every write would surface "
                    "the problem one request later, somewhere else."
                ),
                request_id=str(body.get("requestId", "")),
            )

        self._session = Session(
            session_token=token,
            csrf_token=csrf,
            user_id=str(body.get("userId", "")),
        )
        return self._session

    @property
    def session(self) -> Session | None:
        return self._session

    # --- calling an operation --------------------------------------------------------------------

    def call(
        self,
        operation_id: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        if_match: int | None = None,
        idempotency_key: str | None = None,
        **path_values: str,
    ) -> Any:
        """Perform one operation from the generated table.

        Addressed by operation id rather than by URL. A caller that built its own URL would be
        maintaining a second copy of the API's shape, and the copy drifts silently — a 404 from a
        path that looks almost right is the least informative failure this API can produce.
        """
        operation = self._operation(operation_id)
        headers: dict[str, str] = {}

        if operation.mutating:
            if self._session is None:
                raise NotAuthenticated(
                    status=401,
                    code="NOT_AUTHENTICATED",
                    detail=(
                        f"{operation_id} changes state and there is no session. Call "
                        "sign_in first; the CSRF header is paired with it and cannot be "
                        "supplied separately."
                    ),
                    request_id="",
                )
            headers[CSRF_HEADER] = self._session.csrf_token
        if if_match is not None:
            headers["If-Match"] = str(if_match)
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        response = self._http.request(
            operation.method,
            operation.render(**path_values),
            json=body,
            params=params,
            headers=headers,
        )
        return self._body(response)

    def _operation(self, operation_id: str) -> Operation:
        try:
            return OPERATIONS[operation_id]
        except KeyError:
            raise KeyError(
                f"no operation {operation_id!r} in this contract. The table is generated from the "
                "published contract, so an operation missing here is one the server does not serve."
            ) from None

    def _body(self, response: httpx.Response) -> Any:
        """Turn a response into a value, an exception, or a `Requested`.

        The 202 branch is the one that matters. Returning its body like any other success is how a
        caller ends up treating "we wrote this down" as "this happened", which is the single
        confusion this whole product exists to prevent.
        """
        if response.status_code == 202:
            payload = self._json(response)
            return Requested(status_location=response.headers.get("Location"), body=payload)
        if response.is_success:
            return self._json(response) if response.content else {}

        payload = self._json(response)
        code = str(payload.get("code", "UNKNOWN"))
        detail = str(payload.get("detail", response.text[:400]))
        request_id = str(payload.get("requestId", ""))

        # A specific type for the two conditions with a specific remedy, and the general one for
        # everything else. Not a class per code: a caller branches on `code`, and a parallel
        # hierarchy would be a second copy of the same closed set, drifting.
        kind: type[ApiProblem] = ApiProblem
        if code == "NOT_AUTHENTICATED":
            kind = NotAuthenticated
        elif code == "STALE_REVISION":
            kind = StaleRevision
        raise kind(status=response.status_code, code=code, detail=detail, request_id=request_id)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            parsed = json.loads(response.content or b"{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
