"""Pure authorization logic: roles, permissions and service-identity boundaries.

No database, no session, no HTTP. These are total functions over an explicit principal and an
explicit target, which means the whole authorization matrix can be tested exhaustively without a
server — and that an authority decision can be recomputed later from what was recorded.

Default is deny. Every function here answers "is this exact principal permitted this exact act on
this exact target", and an unrecognized combination is a refusal rather than a pass-through.
"""

from .events import (
    EVENT_PRODUCER_ACL,
    EventSubmissionError,
    assert_may_submit_event,
    may_submit_event,
)
from .principals import (
    HumanPrincipal,
    MachinePrincipal,
    Principal,
    ServiceIdentity,
)
from .roles import (
    ROLE_PERMISSIONS,
    AuthorizationError,
    Permission,
    Role,
    assert_permitted,
    permissions_for,
    role_permits,
)

__all__ = [
    "EVENT_PRODUCER_ACL",
    "ROLE_PERMISSIONS",
    "AuthorizationError",
    "EventSubmissionError",
    "HumanPrincipal",
    "MachinePrincipal",
    "Permission",
    "Principal",
    "Role",
    "ServiceIdentity",
    "assert_may_submit_event",
    "assert_permitted",
    "may_submit_event",
    "permissions_for",
    "role_permits",
]
