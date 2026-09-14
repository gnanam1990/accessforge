"""Fenced baseline build ownership. CAPTURED is not retained, deployable or reader evidence."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.canonical import digest

from . import execution_approvals


class Refused(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Claim:
    attempt_id: str
    run_id: str
    worker_token: str
    epoch: int


def read_binding(
    conn: psycopg.Connection[Any], *, workspace_id: str, run_id: str
) -> dict[str, str]:
    run = conn.execute("SELECT * FROM run WHERE id=%s FOR UPDATE", (run_id,)).fetchone()
    if (
        run is None
        or str(run["workspace_id"]) != workspace_id
        or run["status"] != "QUEUED"
        or run["cancel_requested_at"] is not None
        or run["quarantined"]
        or conn.execute("SELECT 1 FROM desktop_lease WHERE run_id=%s", (run_id,)).fetchone()
        or conn.execute("SELECT 1 FROM candidate_run_binding WHERE run_id=%s", (run_id,)).fetchone()
    ):
        raise Refused("baseline preparation requires an unleased approved queued baseline")
    seal = conn.execute(
        "SELECT id,project_id,source_snapshot_id FROM sealed_manifest WHERE run_id=%s", (run_id,)
    ).fetchone()
    if seal is None:
        raise Refused("original baseline seal is unavailable")
    conn.execute("SELECT id FROM approval WHERE id=%s FOR SHARE", (run["authorization_id"],))
    manifest = execution_approvals.assert_authorized(
        conn, sealed_manifest_id=str(seal["id"]), run_id=run_id, workspace_id=workspace_id
    )
    if (
        digest(manifest) != run["manifest_digest"]
        or manifest["authorizationId"] != str(run["authorization_id"])
        or str(seal["project_id"]) != str(run["project_id"])
    ):
        raise Refused("baseline run and its original authority/manifest differ")
    return dict(
        workspace_id=workspace_id,
        run_id=run_id,
        project_id=str(seal["project_id"]),
        manifest_digest=run["manifest_digest"],
        source_snapshot_id=str(seal["source_snapshot_id"]),
        source_tree_digest=manifest["sourceTreeDigest"],
        expected_artifact_digest=manifest["buildArtifactDigest"],
    )


def claim(
    conn: psycopg.Connection[Any],
    *,
    binding: dict[str, str],
    source_archive_digest: str,
    policy_digest: str,
    image_id: str,
    daemon_endpoint: str,
    daemon_id: str,
) -> Claim:
    if any(not re.fullmatch(r"[a-f0-9]{64}", d) for d in (source_archive_digest, policy_digest)):
        raise Refused("invalid baseline digest")
    with conn.transaction():
        if (
            read_binding(conn, workspace_id=binding["workspace_id"], run_id=binding["run_id"])
            != binding
        ):
            raise Refused("baseline source preparation no longer matches original authority")
        value = Claim(str(uuid.uuid4()), binding["run_id"], str(uuid.uuid4()), 1)
        inserted = conn.execute(
            "INSERT INTO baseline_build_attempt(id,workspace_id,run_id,worker_token,binding,"
            "source_archive_digest,"
            "policy_digest,image_id,daemon_endpoint,daemon_id,state,lease_expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CLAIMED',"
            "clock_timestamp()+interval '180 seconds') "
            "ON CONFLICT(run_id) DO NOTHING RETURNING id",
            (
                value.attempt_id,
                binding["workspace_id"],
                value.run_id,
                value.worker_token,
                Jsonb(binding),
                source_archive_digest,
                policy_digest,
                image_id,
                daemon_endpoint,
                daemon_id,
            ),
        ).fetchone()
        if inserted is None:
            raise Refused("baseline already has a build attempt; no implicit retry")
        return value


def _owned(
    conn: psycopg.Connection[Any], value: Claim, state: str, *, authorize: bool = True
) -> dict[str, Any]:
    # Keep run-before-attempt lock ordering consistent with desktop lease admission.
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (value.run_id,))
    row = conn.execute(
        "SELECT * FROM baseline_build_attempt WHERE id=%s FOR UPDATE", (value.attempt_id,)
    ).fetchone()
    if (
        row is None
        or str(row["run_id"]) != value.run_id
        or str(row["worker_token"]) != value.worker_token
        or row["epoch"] != value.epoch
        or row["state"] != state
    ):
        raise Refused("baseline claim is foreign, fenced or completed")
    if (
        authorize
        and read_binding(conn, workspace_id=str(row["workspace_id"]), run_id=value.run_id)
        != row["binding"]
    ):
        raise Refused("baseline authority changed")
    clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
    assert clock is not None
    moment = clock["now"]
    if row["lease_expires_at"] <= moment:
        raise Refused("baseline claim expired")
    return dict(row)


def dispatch(
    conn: psycopg.Connection[Any],
    *,
    value: Claim,
    binding: dict[str, str],
    source_archive_digest: str,
    policy_digest: str,
    image_id: str,
    daemon_endpoint: str,
    daemon_id: str,
) -> None:
    with conn.transaction():
        row = _owned(conn, value, "CLAIMED")
        expected = dict(
            binding=binding,
            source_archive_digest=source_archive_digest,
            policy_digest=policy_digest,
            image_id=image_id,
            daemon_endpoint=daemon_endpoint,
            daemon_id=daemon_id,
        )
        if any(row[k] != v for k, v in expected.items()):
            raise Refused("baseline dispatch bytes or execution boundary changed")
        conn.execute(
            "UPDATE baseline_build_attempt SET state='DISPATCHED' WHERE id=%s", (value.attempt_id,)
        )


def created(
    conn: psycopg.Connection[Any],
    *,
    value: Claim,
    container_id: str,
    platform: str,
    image_id: str,
    daemon_endpoint: str,
    daemon_id: str,
) -> None:
    with conn.transaction():
        row = _owned(conn, value, "DISPATCHED")
        if row["container_id"] is not None or (image_id, daemon_endpoint, daemon_id) != (
            row["image_id"],
            row["daemon_endpoint"],
            row["daemon_id"],
        ):
            raise Refused("baseline process identity changed or was already recorded")
        conn.execute(
            "UPDATE baseline_build_attempt SET container_id=%s,platform=%s WHERE id=%s",
            (container_id, platform, value.attempt_id),
        )


def captured(
    conn: psycopg.Connection[Any],
    *,
    value: Claim,
    container_id: str,
    platform: str,
    source_archive_digest: str,
    artifact_digest: str,
    policy_digest: str,
    image_id: str,
    daemon_endpoint: str,
    daemon_id: str,
) -> None:
    with conn.transaction():
        row = _owned(conn, value, "DISPATCHED")
        expected = dict(
            container_id=container_id,
            platform=platform,
            source_archive_digest=source_archive_digest,
            policy_digest=policy_digest,
            image_id=image_id,
            daemon_endpoint=daemon_endpoint,
            daemon_id=daemon_id,
        )
        if (
            any(row[k] != v for k, v in expected.items())
            or artifact_digest != row["binding"]["expected_artifact_digest"]
        ):
            raise Refused("baseline capture differs from original build/process inputs")
        conn.execute(
            "UPDATE baseline_build_attempt SET state='CAPTURED',artifact_digest=%s,"
            "cleanup_confirmed=true,finished_at=clock_timestamp() WHERE id=%s",
            (artifact_digest, value.attempt_id),
        )


def fail(conn: psycopg.Connection[Any], *, value: Claim, cleanup_confirmed: bool) -> None:
    with conn.transaction():
        _owned(conn, value, "DISPATCHED", authorize=False)
        conn.execute(
            "UPDATE baseline_build_attempt SET state=%s,epoch=epoch+1,cleanup_confirmed=%s,"
            "failure_code=%s,finished_at=clock_timestamp() WHERE id=%s",
            (
                "FAILED" if cleanup_confirmed else "UNKNOWN",
                cleanup_confirmed,
                "BUILD_FAILED" if cleanup_confirmed else "CLEANUP_UNCONFIRMED",
                value.attempt_id,
            ),
        )


def fence_expired(conn: psycopg.Connection[Any], *, now: datetime | None = None) -> int:
    return conn.execute(
        "UPDATE baseline_build_attempt SET state='UNKNOWN',epoch=epoch+1,"
        "failure_code='LEASE_EXPIRED',finished_at=clock_timestamp() "
        "WHERE state IN ('CLAIMED','DISPATCHED') "
        "AND lease_expires_at<=coalesce(%s,clock_timestamp())",
        (now,),
    ).rowcount
