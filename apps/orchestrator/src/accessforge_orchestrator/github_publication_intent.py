"""Commit a one-shot local publication reservation; no remote dispatch is enabled.

Missing response after commit means UNKNOWN, never retryable. An existing reservation is not a
lease that expires or a job to resume. Remote checks and retained-byte verification must still
be composed by the outbound controller before its one call. This module cannot publish.
"""

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg

from accessforge_domain.authorization import HumanPrincipal
from accessforge_domain.states import ApprovalScope
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import approvals, github_previews, workspace_connection

from .github_access import CreatedCheck
from .github_connections import _authorize
from .github_preview_service import prepare_check_preview


@dataclass(frozen=True, slots=True)
class PublicationRecovery:
    """Point-in-time local observation; not a receipt or permission to retry."""

    local_state: Literal["RECORDED", "NOT_OBSERVED"]
    intent_id: str | None = None
    original_preview_id: str | None = None
    preview_digest: str | None = None
    created_at: str | None = None
    remote_outcome: Literal["UNKNOWN"] = "UNKNOWN"
    retry_allowed: Literal[False] = False
    # A historical confirmed create does not establish current existence, contents or uniqueness.
    original_creation: CreatedCheck | None = None


def read_publication_state(
    database_url: str,
    *,
    principal: HumanPrincipal,
    preview_id: str,
    _connection: psycopg.Connection[Any] | None = None,
) -> PublicationRecovery:
    """Recover a lost reservation response using the original request's preview ID.

    Also resolves a later preview to the original run/App/repository create slot. Reads require
    a current owner/session, but not current publication consent or an active binding: revocation
    must stop writes, not hide historical ambiguity. No row is not proof of no remote write
    (restore, concurrent commit or workspace deletion may have removed the relevant local view).
    """
    try:
        if str(UUID(preview_id)) != preview_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise github_previews.Refused("publication preview identity unavailable") from None
    connection = (
        nullcontext(_connection)
        if _connection is not None
        else workspace_connection(database_url, principal.workspace_id)
    )
    with connection as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        _authorize(conn, principal)
        row = conn.execute(
            "SELECT * FROM github_publication_intent WHERE preview_id=%s", (preview_id,)
        ).fetchone()
        if row is None:
            available = conn.execute(
                "SELECT id FROM github_publication_preview WHERE id=%s", (preview_id,)
            ).fetchone()
            if available is not None:
                # Never trust an alias's embedded identities before validating its stored hash.
                stored = github_previews.read(conn, preview_id=preview_id)
                identity = stored["preview"]["identity"]
                row = conn.execute(
                    "SELECT * FROM github_publication_intent WHERE workspace_id=%s "
                    "AND app_id=%s AND repository_id=%s AND run_id=%s",
                    (
                        principal.workspace_id,
                        int(identity["appId"]),
                        int(identity["repositoryId"]),
                        stored["run_id"],
                    ),
                ).fetchone()
        if row is None:
            return PublicationRecovery(local_state="NOT_OBSERVED")
        receipt = conn.execute(
            "SELECT * FROM github_publication_receipt WHERE intent_id=%s", (row["id"],)
        ).fetchone()
        return PublicationRecovery(
            local_state="RECORDED",
            intent_id=str(row["id"]),
            original_preview_id=str(row["preview_id"]),
            preview_digest=row["preview_digest"],
            created_at=to_rfc3339_utc(row["created_at"]),
            original_creation=None
            if receipt is None
            else CreatedCheck(
                str(receipt["intent_id"]),
                receipt["check_run_id"],
                receipt["payload_digest"],
                to_rfc3339_utc(receipt["observed_at"]),
                receipt["token_revoked"],
            ),
        )


def reserve_publication(
    database_url: str,
    *,
    principal: HumanPrincipal,
    preview_id: str,
    approval_id: str,
    expected_digest: str,
) -> str:
    """Consume an exact locally rechecked approval and persist a non-reusable create slot.

    Caller must be the original approving owner, with a current session and membership. Row
    locks serialize revocation against this transaction, not against future network I/O. Only
    return after commit; a commit/response error must be reconciled by reading durable state.
    Neither this return value nor a stored row is sufficient outbound authority on its own.
    """
    with workspace_connection(database_url, principal.workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        _authorize(conn, principal)
        stored = github_previews.read(conn, preview_id=preview_id)
        if stored["preview_digest"] != expected_digest:
            raise github_previews.Refused("reviewed preview digest differs")
        current = prepare_check_preview(
            database_url,
            principal=principal,
            binding_id=str(stored["binding_id"]),
            run_id=str(stored["run_id"]),
            _connection=conn,
        )
        if current != stored["preview"]:
            raise github_previews.Refused("preview became stale; create and review a new preview")
        # Lock the original consent before loading it through the shared domain checker.
        row = conn.execute(
            "SELECT id FROM approval WHERE id=%s FOR SHARE", (approval_id,)
        ).fetchone()
        if row is None:
            raise github_previews.Refused("publication approval unavailable")
        approval = approvals.load_for_check(conn, approval_id=approval_id)
        if approval.actor_id != principal.user_id:
            raise github_previews.Refused("publication requires its original approving owner")
        clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
        assert clock is not None
        approval.check(
            now=to_rfc3339_utc(clock["now"]),
            scope=ApprovalScope.GITHUB_PUBLISH,
            workspace_id=principal.workspace_id,
            target_id=preview_id,
            target_digest=expected_digest,
            current_revision=0,
        )
        identity = current["identity"]
        intent_id = str(uuid4())
        # ON CONFLICT does not return an existing ID: no replay is ever mistaken for the winner.
        inserted = conn.execute(
            "INSERT INTO github_publication_intent(id,workspace_id,app_id,repository_id,run_id,"
            "preview_id,approval_id,preview_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING id",
            (
                intent_id,
                principal.workspace_id,
                int(identity["appId"]),
                int(identity["repositoryId"]),
                stored["run_id"],
                preview_id,
                approval_id,
                expected_digest,
            ),
        ).fetchone()
        if inserted is None:
            raise github_previews.Refused("publication already reserved; reconcile, never retry")
        conn.execute(
            "INSERT INTO audit_event(workspace_id,actor_user,action,target_kind,target_id,"
            "outcome,occurred_at,detail) VALUES(%s,%s,'GITHUB_PUBLICATION_RESERVE',"
            "'github_publication_intent',%s,'ALLOWED',clock_timestamp(),'{}')",
            (principal.workspace_id, principal.user_id, intent_id),
        )
    return intent_id
