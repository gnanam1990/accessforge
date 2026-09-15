"""Explicit approved-preview publisher, with no endpoint, scheduler or credential discovery.

Only the trusted operator service supplies its principal, App JWT and bounded object store.
The durable reservation is made once immediately before create. No exception authorizes replay.
Remote authority is point-in-time: local revocation cannot retract an HTTP request already sent.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import psycopg

from accessforge_domain.authorization import HumanPrincipal
from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import evaluations, github_previews, workspace_connection
from accessforge_persistence.evidence.objectstore import (
    MAX_ARTIFACT_BYTES,
    artifact_key,
    compute_digest,
)

from .execution_artifacts import ExecutionArtifactStore
from .github_access import CreatedCheck, RepositoryScope, _CreateCheck, _repository_operation
from .github_connections import _authorize
from .github_preview_service import prepare_check_preview
from .github_publication_intent import reserve_publication


class PublicationUnconfirmed(Exception):
    """Static failure; use original preview recovery. Never automatically retry publication."""


def read_creation_receipt(
    database_url: str, *, principal: HumanPrincipal, intent_id: str
) -> CreatedCheck | None:
    """Historical original-create confirmation, not current existence or retry authority.

    Missing receipt remains UNKNOWN. Reads work after publication consent/binding revocation
    for a current authorized owner, including after the original preview/run was deleted.
    """
    try:
        if str(UUID(intent_id)) != intent_id:
            raise ValueError
        with workspace_connection(database_url, principal.workspace_id) as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            _authorize(conn, principal)
            row = conn.execute(
                "SELECT * FROM github_publication_receipt WHERE intent_id=%s", (intent_id,)
            ).fetchone()
            if row is None:
                return None
            return CreatedCheck(
                str(row["intent_id"]),
                row["check_run_id"],
                row["payload_digest"],
                to_rfc3339_utc(row["observed_at"]),
                row["token_revoked"],
            )
    except Exception:
        raise PublicationUnconfirmed("original publication receipt unavailable") from None


def _verify_retained(
    conn: psycopg.Connection[Any], store: ExecutionArtifactStore, preview: dict[str, Any]
) -> None:
    if preview["runStatus"] != "COMPLETED":
        return  # Nonfinal checks carry no evaluation/PASS claim.
    identity = preview["identity"]
    evaluation = evaluations.read(conn, run_id=identity["runId"])
    if evaluation is None or evaluation["snapshotDigest"] != preview["evaluationDigest"]:
        raise PublicationUnconfirmed("original evaluation unavailable")
    snapshot = evaluation["snapshot"]
    expected = snapshot.get("artifacts")
    if not isinstance(expected, list) or not 1 <= len(expected) <= 32:
        raise PublicationUnconfirmed("bounded original evidence set unavailable")
    rows = conn.execute(
        "SELECT * FROM evidence_artifact WHERE workspace_id=%s AND run_id=%s AND attempt_id=%s "
        "ORDER BY id FOR SHARE",
        (identity["workspaceId"], identity["runId"], snapshot["attemptId"]),
    ).fetchall()
    original = {a["artifactId"]: a for a in expected}
    if len(original) != len(expected) or len(rows) != len(expected):
        raise PublicationUnconfirmed("original evidence membership differs")
    for row in rows:
        recorded = original.get(str(row["id"]))
        if (
            recorded
            != {
                "artifactId": str(row["id"]),
                "kind": row["kind"],
                "producerId": row["producer_id"],
                "digest": row["content_digest"],
            }
            or row["state"] != "PROMOTED"
            or row["retention"] != "RETAINED"
            or row["manifest_digest"] != identity["manifestDigest"]
            or not 0 <= row["size_bytes"] <= MAX_ARTIFACT_BYTES
            or row["object_key"]
            != artifact_key(
                workspace_id=identity["workspaceId"],
                run_id=identity["runId"],
                attempt_id=snapshot["attemptId"],
                kind=row["kind"],
                content_digest=row["content_digest"],
            )
        ):
            raise PublicationUnconfirmed("original retained artifact binding differs")
        payload = store.get_bounded(key=row["object_key"], max_bytes=MAX_ARTIFACT_BYTES)
        if len(payload) != row["size_bytes"] or compute_digest(payload) != row["content_digest"]:
            raise PublicationUnconfirmed("original retained artifact bytes unavailable")


def publish_preview(
    database_url: str,
    *,
    principal: HumanPrincipal,
    preview_id: str,
    approval_id: str,
    expected_digest: str,
    app_jwt: str,
    store: ExecutionArtifactStore,
    allow_temporary_token_issuance: bool = False,
    _transport: httpx.BaseTransport | None = None,
) -> CreatedCheck:
    """Recheck -> scope-limited token -> fresh evidence/consent/slot -> one POST -> receipt.

    A lost reservation, create, revocation or receipt-commit response is unconfirmed. Recover
    by original preview ID; a known-ID read match is not proof of unique creation or retry safety.
    No App token is returned or persisted. Creation confirmation is retained only after cleanup.
    """
    if allow_temporary_token_issuance is not True or not isinstance(principal, HumanPrincipal):
        raise PublicationUnconfirmed("explicit trusted publication invocation required")
    try:
        with workspace_connection(database_url, principal.workspace_id) as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            _authorize(conn, principal)
            stored = github_previews.read(conn, preview_id=preview_id)
            preview = stored["preview"]
            if stored["preview_digest"] != expected_digest:
                raise PublicationUnconfirmed("reviewed preview differs")
            current = prepare_check_preview(
                database_url,
                principal=principal,
                binding_id=str(stored["binding_id"]),
                run_id=str(stored["run_id"]),
                _connection=conn,
            )
            if current != preview:
                raise PublicationUnconfirmed("reviewed preview is stale")
            identity = preview["identity"]
            if (
                conn.execute(
                    "SELECT 1 FROM github_publication_intent WHERE workspace_id=%s "
                    "AND app_id=%s AND repository_id=%s AND run_id=%s",
                    (
                        principal.workspace_id,
                        int(identity["appId"]),
                        int(identity["repositoryId"]),
                        identity["runId"],
                    ),
                ).fetchone()
                is not None
            ):
                raise PublicationUnconfirmed("original publication is already reserved")
        identity = preview["identity"]
        scope = RepositoryScope(
            int(identity["appId"]),
            int(identity["installationId"]),
            int(identity["accountId"]),
            int(identity["repositoryId"]),
            identity["owner"],
            identity["repository"],
        )

        def authorize() -> str:
            with workspace_connection(database_url, principal.workspace_id) as conn:
                conn.execute("SET LOCAL statement_timeout='5s'")
                _authorize(conn, principal)
                _verify_retained(conn, store, preview)
            # Reconstruct the preview and recheck original owner/approval after retention I/O.
            # Only a newly committed slot returns; existing slots are never dispatched again.
            return reserve_publication(
                database_url,
                principal=principal,
                preview_id=preview_id,
                approval_id=approval_id,
                expected_digest=expected_digest,
            )

        receipt = _repository_operation(
            scope,
            app_jwt=app_jwt,
            allow_temporary_token_issuance=True,
            commit_sha=identity["sourceSha"],
            _transport=_transport,
            create=_CreateCheck(preview["request"]["body"], authorize),
        )
        if not isinstance(receipt, CreatedCheck):
            raise PublicationUnconfirmed("original create confirmation unavailable")
        with workspace_connection(database_url, principal.workspace_id) as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            # Retaining a known result is not a new publication; revocation must not erase it.
            intent = conn.execute(
                "SELECT * FROM github_publication_intent WHERE id=%s FOR UPDATE",
                (receipt.intent_id,),
            ).fetchone()
            if (
                intent is None
                or str(intent["preview_id"]) != preview_id
                or intent["preview_digest"] != expected_digest
                or str(intent["approval_id"]) != approval_id
                or receipt.payload_digest != digest(preview["request"]["body"])
                or receipt.token_revoked is not True
            ):
                raise PublicationUnconfirmed("original create receipt binding unavailable")
            conn.execute(
                "INSERT INTO github_publication_receipt "
                "(intent_id,workspace_id,check_run_id,payload_digest,observed_at,token_revoked) "
                "VALUES(%s,%s,%s,%s,%s,true)",
                (
                    receipt.intent_id,
                    principal.workspace_id,
                    receipt.check_run_id,
                    receipt.payload_digest,
                    receipt.observed_at,
                ),
            )
        return receipt
    except Exception:
        # This includes uncertain local commit, HTTP response and cleanup. The caller retains
        # its original preview identity; never expose raw provider, database or credential errors.
        raise PublicationUnconfirmed(
            "publication unconfirmed; recover original preview, never automatically redispatch"
        ) from None
