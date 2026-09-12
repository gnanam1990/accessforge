"""Exact approvals, stored and rechecked.

`accessforge_domain.authority.Approval` already decides whether an approval authorizes an act; it is
pure and complete and it was not reachable from anything, because nothing stored an approval. This
is the storage, and it is deliberately thin: the checking stays in the domain so that the rule --
every field compared, a changed digest is not a weaker authorization but no authorization -- has one
implementation rather than one per caller.

The recheck happens at dispatch, not at creation. An approval is a decision about a state of the
world, and between the decision and the act the target can move: the source can be rebuilt, the
patch can be edited, the approver can change their mind. `load_for_check` reads the row fresh for
exactly that reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.authority import Approval
from accessforge_domain.states import ApprovalScope
from accessforge_domain.timestamps import to_rfc3339_utc


class ApprovalError(Exception):
    """An approval could not be recorded or is not there to read."""


def record_approval(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    scope: ApprovalScope,
    actor_id: str,
    target_id: str,
    target_digest: str,
    expected_revision: int,
    expires_at: str,
) -> str:
    """Store one exact approval.

    Binds all of `{scope, actor, workspace, target, digest, revision, expiry}`, because that tuple
    is what makes it exact. An approval missing any of them authorizes a class of acts rather than
    one act, and a class of acts is what a standing permission is -- which this deliberately is not.
    """
    approval_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO approval
            (id, workspace_id, scope, actor_user, target_id, target_digest,
             expected_revision, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            approval_id,
            workspace_id,
            str(scope),
            actor_id,
            target_id,
            target_digest,
            expected_revision,
            expires_at,
        ),
    )
    return approval_id


def load_for_check(conn: psycopg.Connection[dict[str, Any]], *, approval_id: str) -> Approval:
    """Read an approval as the domain type that knows how to refuse.

    Returns the domain object rather than a row so that no caller can accidentally check three of
    the seven fields. `revoked` is derived here: the column is a timestamp, and a caller comparing
    it to None themselves is a caller who might forget to.
    """
    row = conn.execute(
        """
        SELECT id, scope, actor_user, workspace_id, target_id, target_digest,
               expected_revision, expires_at, revoked_at
          FROM approval WHERE id = %s
        """,
        (approval_id,),
    ).fetchone()
    if row is None:
        # Uniform with the rest of the product: an approval in another workspace is invisible under
        # row-level security, and saying "not found" for both avoids confirming it exists.
        raise ApprovalError(f"no approval {approval_id} is visible here")
    return Approval(
        approval_id=str(row["id"]),
        scope=ApprovalScope(str(row["scope"])),
        actor_id=str(row["actor_user"]),
        workspace_id=str(row["workspace_id"]),
        target_id=str(row["target_id"]),
        target_digest=str(row["target_digest"]),
        expected_revision=int(row["expected_revision"]),
        expires_at=to_rfc3339_utc(row["expires_at"]),
        revoked=row["revoked_at"] is not None,
    )


def revoke_approval(
    conn: psycopg.Connection[dict[str, Any]], *, approval_id: str, now: str | None = None
) -> bool:
    """Withdraw an approval. Idempotent; returns whether this call was the one that did it.

    Revocation does not undo what was already done under the approval -- a candidate already built
    stays built. It stops the next act, which is the only thing revocation can honestly promise.
    """
    moment = now or to_rfc3339_utc(datetime.now(UTC))
    affected = conn.execute(
        "UPDATE approval SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
        (moment, approval_id),
    ).rowcount
    return affected == 1
