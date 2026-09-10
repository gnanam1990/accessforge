"""Authentication and authorization at the HTTP boundary.

Authority is resolved in one direction only: a session establishes *who*, the route establishes
*where*, and live membership establishes *what*. Nothing in a request body participates in that
decision.
"""

from .enrollment import (
    EnrollmentError,
    IssuedEnrollment,
    create_enrollment,
    device_may_be_admitted,
    redeem_enrollment,
    revoke_device_admission,
    revoke_device_credential,
)
from .membership import (
    MembershipError,
    assert_route_matches_body,
    record_audit_event,
    require_permission,
    resolve_human_principal,
)
from .sessions import (
    CSRF_HEADER,
    SESSION_COOKIE,
    AuthenticatedSession,
    IssuedSession,
    SessionError,
    issue_session,
    resolve_session,
    revoke_all_sessions_for_user,
    revoke_session,
    rotate_session,
    verify_csrf,
)

__all__ = [
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "AuthenticatedSession",
    "EnrollmentError",
    "IssuedEnrollment",
    "IssuedSession",
    "MembershipError",
    "SessionError",
    "assert_route_matches_body",
    "create_enrollment",
    "device_may_be_admitted",
    "issue_session",
    "record_audit_event",
    "redeem_enrollment",
    "require_permission",
    "resolve_human_principal",
    "resolve_session",
    "revoke_all_sessions_for_user",
    "revoke_device_admission",
    "revoke_device_credential",
    "revoke_session",
    "rotate_session",
    "verify_csrf",
]
