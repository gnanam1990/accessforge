"""Principals: who is acting.

Humans and machines are separate types, not one type with a flag. A desktop lease credential must
never be usable where a human session is expected, and the type system is the cheapest place to
make that true — a function that takes a ``HumanPrincipal`` cannot be handed a runner's token.

Every principal is bound to exactly one workspace. Workspace access is derived from membership plus
route context and never read from a request body, so there is no constructor here that accepts a
caller-supplied workspace claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .roles import Permission, Role, role_permits


class ServiceIdentity(StrEnum):
    """Non-human identities, each with its own narrow purpose.

    These are separate identities rather than one service account, because the whole evidence model
    depends on the supervisor and the independent observer being unable to impersonate each other.
    """

    ORCHESTRATOR = "ORCHESTRATOR"
    """Plans and drives runs. No patch authority, no publication secret."""

    BUILDER = "BUILDER"
    """Builds candidates in isolation. No control-plane writes, no runner credentials."""

    SUPERVISOR = "SUPERVISOR"
    """Admits and bounds navigator actions on a leased desktop. Submits reader, action, preflight
    and lifecycle records — never application receipts."""

    OBSERVER = "OBSERVER"
    """Independently verifies application state. Submits receipts and observer assertions — never
    operating-system actions."""

    INGESTION = "INGESTION"
    """The single trusted sequencer per attempt. Assigns canonical sequence and chains hashes."""

    NAVIGATOR = "NAVIGATOR"
    """Proposes one assistive-technology action at a time from sealed inputs. Submits no evidence
    and holds no credentials of its own."""


@dataclass(frozen=True, slots=True)
class HumanPrincipal:
    """An authenticated person acting in one workspace through a browser session."""

    user_id: str
    workspace_id: str
    role: Role
    session_id: str

    def permits(self, permission: Permission) -> bool:
        return role_permits(self.role, permission)


@dataclass(frozen=True, slots=True)
class MachinePrincipal:
    """A service identity, scoped as narrowly as its job allows.

    ``run_id`` and ``lease_id`` are present when the credential is bound to one run or one desktop
    lease. A supervisor credential for run A must not act on run B, which is why these are part of
    the principal rather than looked up later from whatever the request claimed.
    """

    service_identity: ServiceIdentity
    workspace_id: str
    credential_id: str
    run_id: str | None = None
    lease_id: str | None = None

    def permits(self, permission: Permission) -> bool:
        """Machine identities hold no workspace role permissions at all.

        A service credential never inherits administrator rights by convenience (FR-014), and a
        desktop lease token cannot approve a patch or browse unrelated artifacts. Anything a
        service is allowed to do is expressed as an explicit capability check elsewhere — never as
        a role lookup that happens to succeed.
        """
        return False


Principal = HumanPrincipal | MachinePrincipal
