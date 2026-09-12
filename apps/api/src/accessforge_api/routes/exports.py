"""Evidence exports.

Authorization is rechecked here on every call, not carried from the request that created the export.
An export is asynchronous and a membership revoked in between must take effect — `assert_may_export`
reads the database each time, and the download route calls it again before handing over any bytes.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.canonical import digest
from accessforge_evidence import to_json
from accessforge_persistence import evidence

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["exports"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


def _store(request: Request) -> evidence.S3ArtifactStore:
    config = request.app.state.config
    settings = evidence.S3Settings(
        endpoint_url=str(config.evidence_endpoint_url),
        access_key=str(config.evidence_access_key),
        secret_key=str(config.evidence_secret_key),
        bucket=str(config.evidence_bucket),
    )
    return evidence.S3ArtifactStore(settings)


@router.post("/exports", status_code=status.HTTP_201_CREATED)
def create_export(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Build a bundle from authoritative stored records.

    There is no parameter through which a caller could supply a summary to be packaged. Everything
    in the bundle is read here, from the database and the object store, because a bundle assembled
    from caller-supplied JSON would attest to whatever the caller said.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.EVIDENCE_EXPORT,
        body,
        frozenset({"runId", "attemptId", "includeArtifactBytes"}),
    )

    try:
        export_request = evidence.ExportRequest(
            workspace_id=workspace_id,
            run_id=str(body["runId"]),
            attempt_id=str(body["attemptId"]),
            requested_by=context.principal.user_id,
            include_artifact_bytes=bool(body.get("includeArtifactBytes", True)),
        )
    except KeyError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "runId and attemptId are required",
            request_id=context.request_id,
        ) from exc

    store = _store(request)
    try:
        bundle, members = evidence.build_bundle(conn, store, export_request)
    except evidence.ExportNotAuthorized as exc:
        raise ProblemDetail(
            ProblemCode.PERMISSION_DENIED, str(exc), request_id=context.request_id
        ) from exc
    except evidence.ExportError as exc:
        raise not_found() from exc
    except evidence.ObjectStoreUnavailable as exc:
        # 503, not 500 and not a bundle with the artifact quietly missing. An unavailable store
        # blocks a complete export; substituting an empty object would produce a bundle claiming
        # evidence it does not carry.
        raise ProblemDetail(
            ProblemCode.DEPENDENCY_UNAVAILABLE,
            f"{exc} A complete export requires the artifact store; a bundle assembled without it "
            "would claim evidence it does not carry.",
            request_id=context.request_id,
        ) from exc

    members["bundle.json"] = to_json(bundle).encode("utf-8")
    members["manifest.canonical.json"] = bundle.manifest_bytes()

    export_id = str(uuid.uuid4())
    bundle_digest = digest(bundle.manifest())
    retention_snapshot = {a.kind: str(a.state) for a in bundle.artifacts}
    conn.execute(
        """
        INSERT INTO evidence_export
            (id, workspace_id, run_id, attempt_id, requested_by, bundle_digest, trust_level,
             signing_key_id, retention_snapshot, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now() + interval '7 days')
        """,
        (
            export_id,
            workspace_id,
            export_request.run_id,
            export_request.attempt_id,
            context.principal.user_id,
            bundle_digest,
            str(bundle.trust_level()),
            # Signing happens in the worker that has the key. Recording the intended key id here
            # keeps the export row honest about which key a reader will need.
            str(request.app.state.config.signing_key_id),
            json.dumps(retention_snapshot),
        ),
    )

    return {
        "exportId": export_id,
        "bundleDigest": bundle_digest,
        "trustLevel": str(bundle.trust_level()),
        "memberCount": len(members),
        "limitations": [
            "A signature attributes this bundle to its issuer. It does not make the contents true.",
            "No bundle establishes that the application is usable by people with disabilities.",
            str(bundle.scope_statement),
        ],
    }


@router.get("/exports/{export_id}")
def get_export(
    workspace_id: str, export_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Export status and limitations.

    Authorization is rechecked even for a read, because this row says what was exported and to whom.
    """
    context = authorize(conn, request, workspace_id, Permission.EVIDENCE_EXPORT)
    evidence.assert_may_export(conn, workspace_id=workspace_id, actor_id=context.principal.user_id)

    row = conn.execute(
        """
        SELECT id, run_id, attempt_id, bundle_digest, trust_level, signing_key_id,
               retention_snapshot, created_at, expires_at
        FROM evidence_export WHERE id = %s
        """,
        (export_id,),
    ).fetchone()
    if row is None:
        raise not_found()

    response.headers["ETag"] = f'"{row["bundle_digest"]}"'
    return {
        "exportId": str(row["id"]),
        "runId": str(row["run_id"]),
        "attemptId": str(row["attempt_id"]),
        "bundleDigest": str(row["bundle_digest"]),
        "trustLevel": str(row["trust_level"]),
        "signingKeyId": str(row["signing_key_id"]),
        # What the server held when the bundle was made. An old export describes a moment; without
        # this a reader would take it as a statement about current retention.
        "retentionAtExport": row["retention_snapshot"],
        "createdAt": str(row["created_at"]),
        "expiresAt": str(row["expires_at"]),
        "verifyWith": "accessforge-verify <bundle> --trust-root <key supplied by the issuer>",
        "limitations": [
            "Obtain the verifying key independently of the bundle. A key that travelled inside it "
            "attributes the bundle to nobody: a forger signs with their own key and embeds it.",
            "The retention state above is what was true at export time, not now.",
        ],
    }
