"""Store/review an exact local preview and issue a separate local GITHUB_PUBLISH approval.

No remote adapter or dispatch function is enabled here. These approvals are not proof of current
GitHub access, retained object-store bytes, or single-use remote intent reconciliation.
"""

from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from accessforge_domain.authorization import HumanPrincipal
from accessforge_domain.states import ApprovalScope
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import approvals, github_previews, workspace_connection

from .github_connections import _authorize
from .github_preview_service import prepare_check_preview


def store_preview(
    database_url: str, *, principal: HumanPrincipal, binding_id: str, run_id: str
) -> dict[str, Any]:
    """Reconstruct and commit exact preview; no caller payload or outcome is accepted."""
    with workspace_connection(database_url, principal.workspace_id) as conn:
        preview = prepare_check_preview(
            database_url,
            principal=principal,
            binding_id=binding_id,
            run_id=run_id,
            _connection=conn,
        )
        preview_id = github_previews.record(conn, preview=preview)
        conn.execute(
            "INSERT INTO audit_event(workspace_id,actor_user,action,target_kind,target_id,"
            "outcome,occurred_at,detail) VALUES(%s,%s,'GITHUB_PREVIEW_CREATE',"
            "'github_publication_preview',%s,'ALLOWED',clock_timestamp(),'{}')",
            (principal.workspace_id, principal.user_id, preview_id),
        )
    return {"previewId": preview_id, "preview": preview}


def approve_preview(
    database_url: str, *, principal: HumanPrincipal, preview_id: str, expected_digest: str
) -> str:
    """Approve only the exact preview shown, after locked fresh local reconstruction.

    One deterministic approval ID per preview prevents concurrent duplicate approvals. A
    repeat approval is refused, not renewed or unrevoked; create/review a new preview instead.
    There is no standing policy, automatic approval at preview creation, or remote write.
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
        clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
        assert clock is not None
        approval_id = str(uuid5(NAMESPACE_URL, "accessforge:github-preview-approval:" + preview_id))
        approvals.record_approval(
            conn,
            workspace_id=principal.workspace_id,
            scope=ApprovalScope.GITHUB_PUBLISH,
            actor_id=principal.user_id,
            target_id=preview_id,
            target_digest=expected_digest,
            expected_revision=0,
            expires_at=to_rfc3339_utc(clock["now"] + timedelta(minutes=10)),
            approval_id=approval_id,
        )
        conn.execute(
            "INSERT INTO audit_event(workspace_id,actor_user,action,target_kind,target_id,"
            "outcome,occurred_at,detail) VALUES(%s,%s,'GITHUB_PREVIEW_APPROVE',"
            "'github_publication_preview',%s,'ALLOWED',clock_timestamp(),'{}')",
            (principal.workspace_id, principal.user_id, preview_id),
        )
    return approval_id
