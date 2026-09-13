"""Explicit per-run operator consent to the pinned SDK's reader startup effects.

Not permission to grant TCC, a standing host grant, RUN_EFFECTS approval or physical proof.
All operations require the caller's workspace-scoped transaction; machine binding additionally
requires the already-authenticated exact live session context, never a browser-supplied session ID.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import execution_approvals, projects


class Refused(Exception):
    """The exact operator consent is absent, stale, revoked or already bound elsewhere."""


def startup_effects() -> dict[str, Any]:
    """Fresh object for a human review surface; changing this contract invalidates old grants."""
    return {
        "schemaVersion": 1,
        "sdk": "@guidepup/guidepup",
        "sdkVersion": "0.34.0",
        "reader": "VoiceOver",
        "effects": [
            "TERMINATE_AND_RESTART_VOICEOVER",
            "MOUNT_GUIDEPUP_READER_PREFERENCES",
            "SDK_INTERNAL_STARTUP_ATTEMPTS",
            "RESTORE_PREFERENCES_DURING_NORMAL_STOP",
        ],
        "requiresDedicatedDesktop": True,
        "doesNotAuthorize": ["GRANT_TCC_PERMISSIONS", "INITIAL_APPLESCRIPT_CONFIGURATION"],
    }


def _actor(conn: psycopg.Connection[Any], workspace_id: str, actor_id: str) -> None:
    row = conn.execute(
        "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s",
        (workspace_id, actor_id),
    ).fetchone()
    if row is None or Permission.INFRASTRUCTURE_OPERATE not in permissions_for(Role(row["role"])):
        raise Refused("operator no longer holds infrastructure authority")


def _context(
    conn: psycopg.Connection[Any], workspace_id: str, run_id: str, runner_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    # Same runner -> run lock order as authenticated session/action admission.
    runner = conn.execute(
        "SELECT * FROM runner WHERE id=%s AND workspace_id=%s FOR UPDATE",
        (runner_id, workspace_id),
    ).fetchone()
    run = conn.execute(
        "SELECT * FROM run WHERE id=%s AND workspace_id=%s FOR UPDATE", (run_id, workspace_id)
    ).fetchone()
    if (
        runner is None
        or run is None
        or runner["revoked_at"] is not None
        or runner["platform"] != "darwin"
        or runner["status"] == "QUARANTINED"
        or run["status"] not in {"QUEUED", "LEASED", "RUNNING"}
        or conn.execute("SELECT 1 FROM runner_action WHERE run_id=%s LIMIT 1", (run_id,)).fetchone()
    ):
        raise Refused("reader startup target unavailable")
    seal = projects.find_sealed_manifest(conn, manifest_digest=str(run["manifest_digest"]))
    if seal is None:
        raise Refused("reader startup requires an exact canonical seal")
    try:
        manifest = execution_approvals.assert_authorized(
            conn,
            sealed_manifest_id=seal.sealed_manifest_id,
            run_id=run_id,
            workspace_id=workspace_id,
        )
    except (
        execution_approvals.Refused,
        projects.ProjectError,
        projects.SealError,
        ValueError,
    ) as exc:
        raise Refused("reader startup execution seal unavailable") from exc
    if runner["profile_digest"] != manifest["runnerProfileDigest"]:
        raise Refused("reader startup profile differs from the approved run")
    return dict(run), dict(runner), manifest


def review_scope(
    conn: psycopg.Connection[Any], *, workspace_id: str, run_id: str, runner_id: str
) -> dict[str, Any]:
    """Read the exact scope for a human decision; never issue or reserve consent."""
    run, runner, manifest = _context(conn, workspace_id, run_id, runner_id)
    approval = conn.execute(
        "SELECT expires_at FROM approval WHERE id=%s", (manifest["authorizationId"],)
    ).fetchone()
    if approval is None:
        raise Refused("execution approval unavailable")
    effects = startup_effects()
    return {
        "runId": str(run["id"]),
        "runnerId": str(runner["id"]),
        "revision": int(run["revision"]),
        "manifestDigest": run["manifest_digest"],
        "desktopSessionKey": runner["session_key"],
        "runnerProfileDigest": runner["profile_digest"],
        "effects": effects,
        "effectsDigest": digest(effects),
        "maximumExpiresAt": to_rfc3339_utc(
            min(
                approval["expires_at"],
                parse_rfc3339_utc(manifest["expiresAt"], field="expiresAt"),
            )
        ),
        "meaning": "REVIEW_SCOPE_ONLY_NOT_STARTUP_CONSENT",
    }


def issue(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    runner_id: str,
    actor_id: str,
    expected_revision: int,
    manifest_digest: str,
    desktop_session_key: str,
    runner_profile_digest: str,
    effects_digest: str,
    expires_at: str,
    dedicated_desktop_acknowledged: bool,
) -> dict[str, Any]:
    run, runner, manifest = _context(conn, workspace_id, run_id, runner_id)
    _actor(conn, workspace_id, actor_id)
    expires = parse_rfc3339_utc(expires_at, field="expiresAt")
    approval = conn.execute(
        "SELECT expires_at FROM approval WHERE id=%s", (manifest["authorizationId"],)
    ).fetchone()
    if (
        type(expected_revision) is not int
        or expected_revision != run["revision"]
        or manifest_digest != run["manifest_digest"]
        or desktop_session_key != runner["session_key"]
        or runner_profile_digest != runner["profile_digest"]
        or effects_digest != digest(startup_effects())
        or dedicated_desktop_acknowledged is not True
        or approval is None
        or expires <= datetime.now(UTC)
        or expires > approval["expires_at"]
        or expires > parse_rfc3339_utc(manifest["expiresAt"], field="expiresAt")
    ):
        raise Refused("operator must review the exact startup scope and bounded expiry")
    if conn.execute("SELECT 1 FROM reader_startup_consent WHERE run_id=%s", (run_id,)).fetchone():
        raise Refused("startup consent already issued; revocation cannot create a new grant")
    consent_id = str(uuid4())
    conn.execute(
        "INSERT INTO reader_startup_consent(id,workspace_id,run_id,runner_id,actor_user,"
        "manifest_digest,desktop_session_key,runner_profile_digest,effects_digest,"
        "dedicated_desktop_acknowledged,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s)",
        (
            consent_id,
            workspace_id,
            run_id,
            runner_id,
            actor_id,
            manifest_digest,
            desktop_session_key,
            runner_profile_digest,
            effects_digest,
            expires,
        ),
    )
    _audit(conn, workspace_id, run_id, actor_id, "READER_STARTUP_CONSENT_ISSUED", consent_id)
    return inspect(conn, run_id=run_id)


def inspect(conn: psycopg.Connection[Any], *, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM reader_startup_consent WHERE run_id=%s", (run_id,)).fetchone()
    if row is None:
        raise LookupError("no reader startup consent")
    return {
        "consentId": str(row["id"]),
        "runId": str(row["run_id"]),
        "runnerId": str(row["runner_id"]),
        "actorId": str(row["actor_user"]),
        "manifestDigest": row["manifest_digest"],
        "desktopSessionKey": row["desktop_session_key"],
        "runnerProfileDigest": row["runner_profile_digest"],
        "effectsDigest": row["effects_digest"],
        "expiresAt": to_rfc3339_utc(row["expires_at"]),
        "revokedAt": None if row["revoked_at"] is None else to_rfc3339_utc(row["revoked_at"]),
        "boundSessionId": None if row["bound_session_id"] is None else str(row["bound_session_id"]),
        "meaning": "STORED_OPERATOR_STARTUP_CONSENT_NOT_PHYSICAL_PROOF",
    }


def revoke(
    conn: psycopg.Connection[Any], *, workspace_id: str, run_id: str, actor_id: str, consent_id: str
) -> dict[str, Any]:
    _actor(conn, workspace_id, actor_id)
    row = conn.execute(
        "UPDATE reader_startup_consent SET revoked_at=clock_timestamp() "
        "WHERE id=%s AND run_id=%s AND workspace_id=%s AND revoked_at IS NULL RETURNING id",
        (consent_id, run_id, workspace_id),
    ).fetchone()
    result = inspect(conn, run_id=run_id)
    if result["consentId"] != consent_id:
        raise Refused("revocation names a different consent")
    if row is not None:
        _audit(conn, workspace_id, run_id, actor_id, "READER_STARTUP_CONSENT_REVOKED", consent_id)
    return result


def bind_and_check(
    conn: psycopg.Connection[Any], *, workspace_id: str, session_id: str
) -> dict[str, Any]:
    """Call only AFTER native-session authentication/_live, within that SAME transaction."""
    session = conn.execute(
        "SELECT t.* FROM supervisor_dispatch_ticket t JOIN supervisor_execution_session s "
        "ON s.ticket_id=t.id AND s.workspace_id=t.workspace_id WHERE t.id=%s AND t.workspace_id=%s "
        "AND t.revoked_at IS NULL AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()",
        (session_id, workspace_id),
    ).fetchone()
    if session is None:
        raise Refused("startup session unavailable")
    run, runner, _ = _context(conn, workspace_id, str(session["run_id"]), str(session["runner_id"]))
    grant = conn.execute(
        "SELECT * FROM reader_startup_consent WHERE run_id=%s FOR UPDATE", (run["id"],)
    ).fetchone()
    if grant is None:
        raise Refused("operator startup consent missing")
    _actor(conn, workspace_id, str(grant["actor_user"]))
    if (
        grant["revoked_at"] is not None
        or grant["expires_at"] <= datetime.now(UTC)
        or grant["runner_id"] != runner["id"]
        or grant["manifest_digest"] != run["manifest_digest"]
        or grant["desktop_session_key"] != runner["session_key"]
        or grant["runner_profile_digest"] != runner["profile_digest"]
        or grant["effects_digest"] != digest(startup_effects())
        or grant["bound_session_id"] not in {None, session["id"]}
    ):
        raise Refused("operator startup consent is stale, revoked or bound elsewhere")
    if grant["bound_session_id"] is None:
        conn.execute(
            "UPDATE reader_startup_consent SET bound_session_id=%s WHERE id=%s",
            (session_id, grant["id"]),
        )
        _audit(
            conn,
            workspace_id,
            str(run["id"]),
            None,
            "READER_STARTUP_CONSENT_BOUND",
            str(grant["id"]),
        )
    return inspect(conn, run_id=str(run["id"]))


def _audit(
    conn: psycopg.Connection[Any],
    workspace_id: str,
    run_id: str,
    actor_id: str | None,
    action: str,
    consent_id: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_event(workspace_id,actor_user,actor_service,action,"
        "target_kind,target_id,outcome,detail) VALUES(%s,%s,%s,%s,'run',%s,'ALLOWED',%s)",
        (
            workspace_id,
            actor_id,
            "desktop-supervisor" if actor_id is None else None,
            action,
            run_id,
            Jsonb({"consentId": consent_id}),
        ),
    )
