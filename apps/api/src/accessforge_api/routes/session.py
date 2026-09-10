"""Sign-in, the current session, and sign-out.

The web shell needs three things the rest of `/v1` deliberately does not provide: a way to obtain a
session, a way to ask *who am I and which workspaces may I enter*, and a way to end the session. All
three are workspace-independent, which is why they live outside the `/v1/workspaces/{id}` prefix.

**Authentication is delegated, and delegation is not the same as implementation.** `issue_session`
has existed since module 03 and does not authenticate anyone; establishing who a person is belongs
to a configured identity provider. No such provider is configured by default, so `POST /v1/sessions`
refuses with `DEPENDENCY_UNAVAILABLE` rather than inventing a credential store. The shell renders
that refusal as what it is — sign-in is unavailable because a dependency is missing — instead of a
login form that could never succeed.

**One provider is implemented, and it is a local-development bridge.** `local-development` accepts
an email and issues a session for a matching, enabled `app_user` **with no secret at all**. That is
an authentication bypass by construction, so:

* it is off unless `ACCESSFORGE_IDENTITY_PROVIDER=local-development` is set explicitly;
* `ApiSettings` refuses that value unless `environment` is `local`, so a staging or production
  deployment cannot start with it enabled even if someone sets the variable;
* while it is off the route answers 503 and names the missing configuration, and never reveals
  whether a given account exists.

It exists so a developer can actually operate the product on their own machine. It is not a sign-in
mode for anything else, and the handoff says so.

**Two cookies, doing different jobs.** The session cookie is `HttpOnly` — script must not be able to
read a credential. The CSRF cookie deliberately is not: the browser echoes it back in a header, the
server compares it against the stored hash, and a cross-site request can send the cookie but cannot
read it in order to set the header. A CSRF token in `localStorage` would survive sign-out; a cookie
is cleared with the session it belongs to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response, status

from accessforge_api.auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SessionError,
    issue_session,
    record_global_audit_event,
    resolve_session,
    revoke_session,
    verify_csrf,
)
from accessforge_api.problems import ProblemCode, ProblemDetail, SafeFieldPolicy
from accessforge_persistence import unscoped_connection, user_connection

router = APIRouter(prefix="/v1", tags=["session"])

#: Readable by script on purpose; see the module docstring. Named distinctly from the session cookie
#: so a reader of a browser inspector cannot mistake one for the other.
CSRF_COOKIE = "accessforge_csrf"

#: The maximum length of an email this route will even look up. A bound before the query, because an
#: unbounded parameter is an unbounded index scan and a very large log line.
MAX_EMAIL_LENGTH = 320


def _config(request: Request) -> Any:
    return request.app.state.config


def _set_session_cookies(
    response: Response, *, session_token: str, csrf_token: str, secure: bool, expires: datetime
) -> None:
    max_age = max(0, int((expires - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response, *, secure: bool) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", httponly=name == SESSION_COOKIE, secure=secure)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def sign_in(request: Request, response: Response, payload: dict[str, Any]) -> dict[str, Any]:
    """Exchange an identity-provider assertion for a browser session.

    The only implemented provider is the local-development bridge described in the module docstring.
    With no provider configured this is a 503 naming the missing configuration, not a 401: the
    caller's credentials were never the problem.
    """
    config = _config(request)
    if config.identity_provider == "none":
        raise ProblemDetail(
            ProblemCode.DEPENDENCY_UNAVAILABLE,
            "no identity provider is configured, so this deployment cannot sign anyone in. This is "
            "a missing dependency rather than a rejected credential: authentication is delegated "
            "to a provider, and none has been supplied.",
        )

    if not isinstance(payload, dict):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "the request body must be a JSON object")
    SafeFieldPolicy(frozenset({"email"})).assert_no_unexpected(payload)

    email = payload.get("email")
    if not isinstance(email, str) or not email.strip() or len(email) > MAX_EMAIL_LENGTH:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "email is required")

    with unscoped_connection(config.database_url) as conn:
        row = conn.execute(
            "SELECT id FROM app_user WHERE lower(email) = lower(%s) AND disabled_at IS NULL",
            (email.strip(),),
        ).fetchone()
        user_id = str(row["id"]) if row is not None else None
        if user_id is not None:
            issued = issue_session(conn, user_id=user_id)

    # The audit row is written on its own connection, after the one above has committed or been
    # rolled back. Recording a denial inside the transaction that then raises would roll the record
    # back with it -- the denials would vanish precisely because they were denials, which is the
    # opposite of what an audit trail is for.
    with unscoped_connection(config.database_url) as conn:
        record_global_audit_event(
            conn,
            action="session.sign-in",
            target_kind="app_user",
            target_id=user_id,
            outcome="ALLOWED" if user_id is not None else "DENIED",
            actor_user=user_id,
            detail={"provider": str(config.identity_provider)},
        )

    if user_id is None:
        # One answer for "no such account" and "account disabled". Distinguishing them turns this
        # route into an account-existence oracle, and the local-development bridge would be the
        # easiest way in the product to enumerate real users.
        raise ProblemDetail(
            ProblemCode.NOT_AUTHENTICATED,
            "sign-in was refused. The same answer is given for an unknown account and a "
            "disabled one, so this response does not tell you whether the address is in use.",
        )

    _set_session_cookies(
        response,
        session_token=issued.session_token,
        csrf_token=issued.csrf_token,
        secure=config.environment != "local",
        expires=issued.expires_at,
    )
    # The tokens are set as cookies and are not repeated in the body. A body copy would reach the
    # console, the network panel of a shared screen, and any log that records a response.
    return {
        "userId": user_id,
        "expiresAt": issued.expires_at.isoformat(),
        "signInMode": str(config.identity_provider),
    }


@router.get("/session")
def read_session(request: Request) -> dict[str, Any]:
    """Who this is, and which workspaces they may enter right now.

    Memberships are read through `user_connection`, whose only widened policy matches the acting
    user's own rows. Revocation therefore takes effect immediately: a workspace removed a moment ago
    is absent from the next answer rather than lingering in a cached navigation.
    """
    config = _config(request)
    with unscoped_connection(config.database_url) as conn:
        try:
            session = resolve_session(conn, session_token=request.cookies.get(SESSION_COOKIE))
        except SessionError as exc:
            raise ProblemDetail(ProblemCode.NOT_AUTHENTICATED, str(exc)) from exc
        user = conn.execute(
            "SELECT email FROM app_user WHERE id = %s AND disabled_at IS NULL",
            (session.user_id,),
        ).fetchone()

    if user is None:
        # The session row survived an account being disabled. Treated as no session at all rather
        # than as a principal with no account.
        raise ProblemDetail(ProblemCode.NOT_AUTHENTICATED, "session is not valid")

    with user_connection(config.database_url, session.user_id) as conn:
        rows = conn.execute(
            """
            SELECT m.workspace_id, m.role, w.name
            FROM workspace_membership m
            JOIN workspace w ON w.id = m.workspace_id
            WHERE m.user_id = %s AND m.revoked_at IS NULL
            ORDER BY w.name, m.workspace_id
            """,
            (session.user_id,),
        ).fetchall()

    return {
        "userId": session.user_id,
        "email": str(user["email"]),
        "workspaces": [
            {
                "workspaceId": str(r["workspace_id"]),
                "name": str(r["name"]),
                "role": str(r["role"]),
            }
            for r in rows
        ],
    }


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def sign_out(request: Request, response: Response) -> None:
    """End this session.

    CSRF-protected like any other mutation. A forced sign-out is a small attack, but it is still an
    attack a cross-site request could mount, and exempting it would make the rule conditional.

    Signing out with no session succeeds and clears the cookies. Reporting 401 would leave a browser
    holding a cookie it cannot use and no way to discard it.

    The cookies are cleared by writing to the **injected** response rather than by constructing a
    new one. An earlier version built a fresh `Response(headers=dict(response.headers))`, and
    `dict()` over a header collection keeps one value per name — so of the two `Set-Cookie`
    deletions, one silently vanished and the browser kept a cookie the server believed it had
    cleared. A real browser found that; nothing in a status-code assertion would have.
    """
    config = _config(request)
    secure = config.environment != "local"
    with unscoped_connection(config.database_url) as conn:
        try:
            session = resolve_session(conn, session_token=request.cookies.get(SESSION_COOKIE))
        except SessionError:
            _clear_session_cookies(response, secure=secure)
            return

        try:
            verify_csrf(session, method="DELETE", csrf_token=request.headers.get(CSRF_HEADER))
        except SessionError as exc:
            raise ProblemDetail(ProblemCode.CSRF_REQUIRED, str(exc)) from exc

        revoke_session(conn, session_id=session.session_id)
        record_global_audit_event(
            conn,
            action="session.sign-out",
            target_kind="user_session",
            target_id=session.session_id,
            outcome="ALLOWED",
            actor_user=session.user_id,
        )

    _clear_session_cookies(response, secure=secure)
