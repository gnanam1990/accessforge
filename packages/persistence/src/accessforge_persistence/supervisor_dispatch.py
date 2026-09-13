"""Short-lived, exact-attempt machine authentication for one receiver admission.

No human session, role permission, observer identity or OS action capability is minted. The
controller sends the secret only to its trusted transport; only its digest is retained. Acceptance
is intentionally not idempotent: a lost response requires reconciliation, never another start.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization import MachinePrincipal, ServiceIdentity
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import runners


class Refused(Exception):
    """Unknown, expired, revoked, consumed or no-longer-authorized ticket."""


@dataclass(frozen=True, slots=True)
class DispatchTicket:
    ticket_id: str
    expires_at: str
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AcceptedDispatch:
    principal: MachinePrincipal
    attempt_id: str
    runner_id: str
    epoch: int


def issue(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    runner_id: str,
    lease_id: str,
    epoch: int,
) -> DispatchTicket:
    """Trusted controller only, in the SAME transaction as its initial commitment."""
    manifest = runners.assert_manual_attempt_authorized(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        attempt_id=attempt_id,
        runner_id=runner_id,
        lease_id=lease_id,
        epoch=epoch,
    )
    now = datetime.now(UTC)
    bounds = conn.execute(
        "SELECT l.deadline_at,a.expires_at FROM desktop_lease l JOIN run r ON r.id=l.run_id "
        "AND r.workspace_id=l.workspace_id JOIN approval a ON a.id=r.authorization_id "
        "WHERE l.id=%s",
        (lease_id,),
    ).fetchone()
    if bounds is None:
        raise Refused("dispatch authority unavailable")
    expires = min(
        now + timedelta(seconds=30),
        bounds["deadline_at"],
        bounds["expires_at"],
        parse_rfc3339_utc(manifest["expiresAt"], field="expiresAt"),
    )
    if expires <= now:
        raise Refused("dispatch authority expired")
    token = secrets.token_urlsafe(32)
    ticket_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO supervisor_dispatch_ticket(id,workspace_id,run_id,attempt_id,runner_id,"
        "lease_id,epoch,token_digest,created_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            ticket_id,
            workspace_id,
            run_id,
            attempt_id,
            runner_id,
            lease_id,
            epoch,
            hashlib.sha256(token.encode()).hexdigest(),
            now,
            expires,
        ),
    )
    return DispatchTicket(ticket_id, to_rfc3339_utc(expires), token)


def accept(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    ticket_id: str,
    token: str,
) -> AcceptedDispatch:
    """Authenticate and consume once, revalidating the committed attempt immediately before use."""
    if len(token) != 43 or not token.isascii():
        raise Refused("dispatch ticket unavailable")
    row = conn.execute(
        "SELECT * FROM supervisor_dispatch_ticket WHERE id=%s", (ticket_id,)
    ).fetchone()
    if (
        row is None
        or str(row["workspace_id"]) != workspace_id
        or not hmac.compare_digest(row["token_digest"], hashlib.sha256(token.encode()).hexdigest())
    ):
        raise Refused("dispatch ticket unavailable")
    # Same order as controller/lease admission. A reader acknowledgement cannot race cancellation
    # under an old run state, or revive a ticket consumed by another receiver.
    conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (row["runner_id"],))
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (row["run_id"],))
    conn.execute(
        "SELECT a.id FROM approval a JOIN run r ON r.authorization_id=a.id "
        "WHERE r.id=%s FOR SHARE OF a",
        (row["run_id"],),
    )
    current = conn.execute(
        "SELECT * FROM supervisor_dispatch_ticket WHERE id=%s FOR UPDATE", (ticket_id,)
    ).fetchone()
    if (
        current is None
        or current["accepted_at"] is not None
        or current["revoked_at"] is not None
        or current["expires_at"] <= datetime.now(UTC)
    ):
        raise Refused("dispatch ticket unavailable")
    try:
        runners.assert_manual_attempt_authorized(
            conn,
            workspace_id=workspace_id,
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            runner_id=str(row["runner_id"]),
            lease_id=str(row["lease_id"]),
            epoch=int(row["epoch"]),
        )
    except runners.DispatchRefused as exc:
        raise Refused("dispatch ticket unavailable") from exc
    accepted = conn.execute(
        "UPDATE supervisor_dispatch_ticket SET accepted_at=clock_timestamp() WHERE id=%s "
        "AND expires_at>clock_timestamp() AND accepted_at IS NULL AND revoked_at IS NULL "
        "RETURNING id",
        (ticket_id,),
    ).fetchone()
    if accepted is None:
        raise Refused("dispatch ticket unavailable")
    conn.execute(
        "INSERT INTO audit_event(workspace_id,actor_service,action,target_kind,target_id,outcome,"
        "detail) VALUES(%s,'supervisor-dispatch-receiver','SUPERVISOR_DISPATCH_ACCEPTED',"
        "'run',%s,'ALLOWED',%s)",
        (
            workspace_id,
            row["run_id"],
            Jsonb(
                {
                    "ticketId": ticket_id,
                    "attemptId": str(row["attempt_id"]),
                    "leaseId": str(row["lease_id"]),
                    "epoch": int(row["epoch"]),
                }
            ),
        ),
    )
    return AcceptedDispatch(
        principal=MachinePrincipal(
            service_identity=ServiceIdentity.SUPERVISOR,
            workspace_id=workspace_id,
            credential_id=ticket_id,
            run_id=str(row["run_id"]),
            lease_id=str(row["lease_id"]),
        ),
        attempt_id=str(row["attempt_id"]),
        runner_id=str(row["runner_id"]),
        epoch=int(row["epoch"]),
    )
