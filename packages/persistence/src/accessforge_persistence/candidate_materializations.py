"""Trusted conversion of captured candidate bytes into canonical source/build input records."""

from __future__ import annotations

import re
from typing import Any

import psycopg

from . import candidate_builds as builds
from . import candidate_regressions as regressions
from . import patches, projects
from .source_intake import SourceIdentity

Refused = builds.BuildClaimRefused


def capture_source(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: builds.BuildClaim,
    tree_digest: str,
    archive_digest: str,
    changed_paths: tuple[str, ...],
) -> None:
    """Called with the broker/preparer's actual bytes in the same transaction as the claim."""
    if not re.fullmatch(r"[a-f0-9]{64}", tree_digest):
        raise Refused("invalid captured candidate tree digest")
    with conn.transaction():
        row = builds._owned(conn, claim, "CLAIMED", None)
        if (
            str(row["patch_id"]) != claim.patch_id
            or str(row["verification_id"]) != claim.verification_id
        ):
            raise Refused("candidate source belongs to another patch or verification")
        patch = patches.load_patch(conn, patch_id=str(row["patch_id"]))
        paths = {change.path for change in patch.changes}
        if (
            archive_digest != row["candidate_archive_digest"]
            or tuple(sorted(set(changed_paths))) != changed_paths
            or not set(changed_paths) <= paths
            or (
                not changed_paths
                and (
                    tree_digest != row["source_tree_digest"]
                    or archive_digest != row["base_archive_digest"]
                )
            )
        ):
            raise Refused("captured candidate source differs from its build claim")
        if conn.execute(
            "SELECT 1 FROM candidate_materialization WHERE build_id=%s", (claim.build_id,)
        ).fetchone():
            raise Refused("candidate source was already captured")
        snapshot_id = projects.record_source_snapshot(
            conn,
            workspace_id=str(row["workspace_id"]),
            project_id=str(row["project_id"]),
            identity=SourceIdentity(
                str(row["source_commit"]), tree_digest, bool(changed_paths), changed_paths
            ),
            requested_revision="approved-patch:" + str(row["patch_digest"]),
        )
        builds._owned(conn, claim, "CLAIMED", None)
        conn.execute(
            "INSERT INTO candidate_materialization(build_id,workspace_id,source_snapshot_id,"
            "source_tree_digest,changed_paths) VALUES (%s,%s,%s,%s,%s)",
            (claim.build_id, row["workspace_id"], snapshot_id, tree_digest, list(changed_paths)),
        )


def publish(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    build_id: str,
    observed_artifact_digest: str,
) -> dict[str, Any]:
    """After a fresh retained-byte read: idempotent metadata, never build re-execution."""
    with conn.transaction():
        build = regressions._authority(conn, build_id, workspace_id)
        row = conn.execute(
            "SELECT * FROM candidate_materialization WHERE build_id=%s FOR UPDATE",
            (build_id,),
        ).fetchone()
        if row is None:
            raise Refused(
                "candidate source capture is absent; no historical provenance is invented"
            )
        source = conn.execute(
            "SELECT * FROM source_snapshot WHERE id=%s FOR SHARE",
            (row["source_snapshot_id"],),
        ).fetchone()
        if (
            observed_artifact_digest != build["artifact_digest"]
            or source is None
            or source["workspace_id"] != build["workspace_id"]
            or source["project_id"] != build["project_id"]
            or source["commit_sha"] != build["source_commit"]
            or source["tree_digest"] != row["source_tree_digest"]
            or source["dirty"] != bool(row["changed_paths"])
            or source["dirty_path_count"] != len(row["changed_paths"])
            or source["requested_revision"] != "approved-patch:" + build["patch_digest"]
        ):
            raise Refused("candidate source/output records no longer match captured bytes")
        if row["build_artifact_id"] is not None:
            artifact = conn.execute(
                "SELECT * FROM build_artifact WHERE id=%s FOR SHARE",
                (row["build_artifact_id"],),
            ).fetchone()
            if (
                artifact is None
                or artifact["workspace_id"] != build["workspace_id"]
                or artifact["project_id"] != build["project_id"]
                or artifact["source_snapshot_id"] != row["source_snapshot_id"]
                or artifact["artifact_digest"] != observed_artifact_digest
                or row["artifact_digest"] != observed_artifact_digest
                or not artifact["identity_observable"]
            ):
                raise Refused("published candidate artifact identity changed")
            regressions._authority(conn, build_id, workspace_id)
            return row
        artifact_id = projects.record_build_artifact(
            conn,
            workspace_id=workspace_id,
            project_id=str(build["project_id"]),
            source_snapshot_id=str(row["source_snapshot_id"]),
            artifact_digest=observed_artifact_digest,
            identity_observable=True,
        )
        regressions._authority(conn, build_id, workspace_id)
        result = conn.execute(
            "UPDATE candidate_materialization SET build_artifact_id=%s,artifact_digest=%s,"
            "published_at=clock_timestamp() WHERE build_id=%s RETURNING *",
            (artifact_id, observed_artifact_digest, build_id),
        ).fetchone()
        assert result is not None
        return result
