"""Immutable trusted-broker comparisons, with permanent retirement and current read authority."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import inspect_patch
from accessforge_domain.timestamps import to_rfc3339_utc

from . import candidate_builds, patches


class ComparisonRefused(ValueError):
    pass


def authorize(
    conn: psycopg.Connection[Any], workspace_id: str, actor_id: str, permission: Permission
) -> None:
    row = conn.execute(
        "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s FOR SHARE",
        (workspace_id, actor_id),
    ).fetchone()
    if row is None or permission not in permissions_for(Role(row["role"])):
        raise ComparisonRefused("current comparison permission unavailable")


def _files(payload: dict[str, Any], patch: patches.PatchProposal) -> None:
    files = payload.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 20 or len(files) != len(patch.changes):
        raise ComparisonRefused("complete bounded comparison files required")
    for file, change in zip(files, patch.changes, strict=True):
        if (
            not isinstance(file, dict)
            or file.get("path") != change.path
            or not isinstance(file.get("unifiedDiff"), str)
        ):
            raise ComparisonRefused("comparison paths differ from the exact proposal")
        for key in ("before", "after"):
            if key not in file:
                raise ComparisonRefused("explicit source sides required")
            side = file.get(key)
            if side is None:
                continue
            if not isinstance(side, dict) or not isinstance(side.get("text"), str):
                raise ComparisonRefused("source text shape unavailable")
            content = side["text"].encode("utf-8")
            if (
                side.get("sha256") != hashlib.sha256(content).hexdigest()
                or type(side.get("byteLength")) is not int
                or side.get("byteLength") != len(content)
                or side.get("mode") not in {"100644", "100755"}
            ):
                raise ComparisonRefused("retained source text does not match its identity")
        after = file.get("after")
        if (change.content is None and after is not None) or (
            change.content is not None and (after is None or after["text"] != change.content)
        ):
            raise ComparisonRefused("comparison after-content differs from the proposal")
        before = file["before"]
        if before is None and after is None:
            raise ComparisonRefused("cannot compare an absent deletion")
        operation = "ADD" if before is None else "DELETE" if after is None else "MODIFY"
        if file.get("operation") != operation or file.get("changed") is not (before != after):
            raise ComparisonRefused("comparison operation or change flag differs from source")
        if after is not None and after["mode"] != (
            change.mode or (before["mode"] if before is not None else "100644")
        ):
            raise ComparisonRefused("comparison mode differs from proposal")


def _view(row: dict[str, Any], patch: patches.PatchProposal) -> dict[str, Any]:
    payload = row["payload"]
    if payload is not None:
        if digest(payload) != row["comparison_digest"] or any(
            payload.get(key) != value
            for key, value in {
                "workspaceId": str(row["workspace_id"]),
                "projectId": str(row["project_id"]),
                "sourceSnapshotId": str(row["source_snapshot_id"]),
                "requestedBy": str(row["prepared_by"]),
                "patchId": patch.patch_id,
                "patchDigest": patch.patch_digest,
                "baseSourceDigest": patch.base_source_digest,
                "baseManifestDigest": patch.base_manifest_digest,
            }.items()
        ):
            raise ComparisonRefused("original comparison identity unavailable")
        _files(payload, patch)
    return {
        "comparisonId": str(row["id"]),
        "comparisonDigest": row["comparison_digest"],
        "patchId": patch.patch_id,
        "patchDigest": row["patch_digest"],
        "baseSourceDigest": row["base_source_digest"],
        "comparison": payload,
        "preparedBy": str(row["prepared_by"]),
        "recordedAt": to_rfc3339_utc(row["created_at"]),
        "retiredAt": None if row["retired_at"] is None else to_rfc3339_utc(row["retired_at"]),
        "meaning": "RETAINED_SOURCE_COMPARISON_NOT_APPROVAL_OR_VERIFICATION",
    }


def retain_prepared(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    patch_id: str,
    actor_id: str,
    prepared: dict[str, Any],
) -> dict[str, Any]:
    """Trusted in-process producer only. No public API accepts a purported source comparison."""
    authorize(conn, workspace_id, actor_id, Permission.PROJECT_CONFIGURE)
    conn.execute("SELECT id FROM patch_proposal WHERE id=%s FOR SHARE", (patch_id,))
    patch = patches.load_patch(conn, patch_id=patch_id)
    baseline = candidate_builds._baseline(conn, patch_id, workspace_id)
    payload = prepared.get("comparison")
    if not isinstance(payload, dict) or prepared.get("comparisonDigest") != digest(payload):
        raise ComparisonRefused("prepared comparison integrity unavailable")
    expected = {
        "schemaVersion": 1,
        "workspaceId": workspace_id,
        "patchId": patch_id,
        "patchDigest": patch.patch_digest,
        "patchRevision": patch.revision,
        "baseManifestDigest": patch.base_manifest_digest,
        "baseSourceDigest": patch.base_source_digest,
        "baseCommitSha": baseline["commit_sha"],
        "sourceSnapshotId": str(baseline["source_snapshot_id"]),
        "projectId": str(baseline["project_id"]),
        "requestedBy": actor_id,
        "meaning": "ORIGINAL_SOURCE_COMPARISON_NOT_APPLICATION_OR_VERIFICATION",
    }
    if (
        any(payload.get(key) != value for key, value in expected.items())
        or not inspect_patch(patch.changes, application_paths=tuple(baseline["paths"])).acceptable
    ):
        raise ComparisonRefused("prepared comparison is stale or outside current scope")
    _files(payload, patch)
    if len(json.dumps(payload, ensure_ascii=True).encode()) > 16 * 1024 * 1024:
        raise ComparisonRefused("serialized comparison exceeds retention budget")
    row = conn.execute(
        "INSERT INTO patch_source_comparison"
        "(id,workspace_id,patch_id,project_id,source_snapshot_id,"
        "patch_digest,base_source_digest,comparison_digest,payload,prepared_by) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(patch_id,patch_digest,base_source_digest) DO NOTHING RETURNING *",
        (
            str(uuid4()),
            workspace_id,
            patch_id,
            baseline["project_id"],
            baseline["source_snapshot_id"],
            patch.patch_digest,
            patch.base_source_digest,
            prepared["comparisonDigest"],
            Jsonb(payload),
            actor_id,
        ),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM patch_source_comparison "
            "WHERE patch_id=%s AND patch_digest=%s AND base_source_digest=%s",
            (patch_id, patch.patch_digest, patch.base_source_digest),
        ).fetchone()
    if row is None:
        raise ComparisonRefused("comparison retention not confirmed")
    return _view(row, patch)


def read(conn: psycopg.Connection[Any], *, patch_id: str) -> dict[str, Any]:
    patch = patches.load_patch(conn, patch_id=patch_id)
    row = conn.execute(
        "SELECT c.* FROM patch_source_comparison c JOIN project p ON p.id=c.project_id "
        "WHERE c.patch_id=%s AND c.patch_digest=%s AND c.base_source_digest=%s "
        "AND (c.retired_at IS NOT NULL OR "
        "(p.revoked_at IS NULL AND p.repository_authorized_by IS NOT NULL))",
        (patch_id, patch.patch_digest, patch.base_source_digest),
    ).fetchone()
    if row is None:
        raise LookupError("no current retained source comparison")
    return _view(row, patch)


def retire(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    patch_id: str,
    comparison_id: str,
    actor_id: str,
) -> dict[str, Any]:
    authorize(conn, workspace_id, actor_id, Permission.WORKSPACE_CONFIGURE)
    current = read(conn, patch_id=patch_id)
    if current["comparisonId"] != comparison_id:
        raise ComparisonRefused("retirement must name the exact comparison read")
    conn.execute(
        "UPDATE patch_source_comparison SET payload=NULL,retired_at=clock_timestamp() "
        "WHERE id=%s AND retired_at IS NULL",
        (comparison_id,),
    )
    return read(conn, patch_id=patch_id)
