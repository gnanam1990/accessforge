"""Revalidate protected baseline seed provenance from original immutable runtime rows."""

from __future__ import annotations

from typing import Any

import psycopg

from accessforge_domain.canonical import digest


def validate_runtime(
    conn: psycopg.Connection[Any], *, run_id: str, workspace_id: str, observation: dict[str, Any]
) -> None:
    runtime = observation.get("baselineRuntime")
    if runtime is None:
        if "baselineRuntime" in observation or "baselineRuntimeDigest" in observation:
            raise ValueError("baseline seed provenance is partial")
        return  # Existing separately observed external fixtures do not invent owned provenance.
    if not isinstance(runtime, dict) or digest(runtime) != observation.get("baselineRuntimeDigest"):
        raise ValueError("baseline seed runtime digest differs")
    parent = conn.execute(
        "SELECT r.*,b.state AS build_state,b.artifact_digest AS captured_artifact "
        "FROM baseline_regression_attempt r JOIN baseline_build_attempt b "
        "ON b.id=r.build_id AND b.workspace_id=r.workspace_id AND b.run_id=r.run_id "
        "WHERE r.run_id=%s AND r.workspace_id=%s",
        (run_id, workspace_id),
    ).fetchone()
    if (
        parent is None
        or parent["state"] not in {"DISPATCHED", "PASSED"}
        or parent["build_state"] != "CAPTURED"
        or parent["captured_artifact"] != parent["artifact_digest"]
    ):
        raise ValueError("baseline seed has no unfenced original runtime")
    rows = conn.execute(
        "SELECT role,container_id,image_id,state FROM baseline_regression_process "
        "WHERE attempt_id=%s AND workspace_id=%s AND role IN ('database','driver','candidate')",
        (parent["id"], workspace_id),
    ).fetchall()
    if {p["role"] for p in rows} != {"database", "driver", "candidate"} or any(
        p["state"] not in {"CREATED", "REMOVED"}
        or not p["container_id"]
        or (p["role"] != "database" and p["image_id"] != parent["image_id"])
        for p in rows
    ):
        raise ValueError("baseline seed process provenance unavailable")
    expected = {
        "attemptId": str(parent["id"]),
        "buildId": str(parent["build_id"]),
        "workerEpoch": parent["epoch"],
        "artifactDigest": parent["artifact_digest"],
        "policyDigest": parent["policy_digest"],
        "daemonEndpoint": parent["daemon_endpoint"],
        "daemonId": parent["daemon_id"],
        "processes": {
            p["role"]: {"containerId": p["container_id"], "imageId": p["image_id"]} for p in rows
        },
    }
    if runtime != expected:
        raise ValueError("baseline seed differs from original runtime/process receipts")
