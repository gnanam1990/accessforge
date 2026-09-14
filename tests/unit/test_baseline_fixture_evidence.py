"""Synthetic historical rows test original provenance matching, not actual seed execution."""

from typing import Any, cast

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_persistence.baseline_fixture_evidence import validate_runtime


@pytest.mark.parametrize(
    "fault", [None, "unknown", "epoch", "process", "image", "artifact", "partial"]
)
def test_baseline_seed_matches_original_runtime_receipts(fault: str | None) -> None:
    parent: dict[str, Any] = dict(
        id="attempt",
        build_id="build",
        epoch=1,
        state="PASSED",
        build_state="CAPTURED",
        captured_artifact="a" * 64,
        artifact_digest="a" * 64,
        policy_digest="p" * 64,
        daemon_endpoint="unix:///fixture.sock",
        daemon_id="daemon",
        image_id="image",
    )
    rows = [
        dict(role=role, container_id=role, image_id="image", state="REMOVED")
        for role in ("database", "driver", "candidate")
    ]
    runtime = dict(
        attemptId="attempt",
        buildId="build",
        workerEpoch=1,
        artifactDigest="a" * 64,
        policyDigest="p" * 64,
        daemonEndpoint="unix:///fixture.sock",
        daemonId="daemon",
        processes={
            r["role"]: {"containerId": r["container_id"], "imageId": r["image_id"]} for r in rows
        },
    )
    observation = dict(baselineRuntime=runtime, baselineRuntimeDigest=digest(runtime))
    if fault == "unknown":
        parent["state"] = "UNKNOWN"
    elif fault == "epoch":
        parent["epoch"] = 2
    elif fault == "process":
        rows[0]["container_id"] = "replacement"
    elif fault == "image":
        parent["image_id"] = "changed"
    elif fault == "artifact":
        parent["captured_artifact"] = "different"
    elif fault == "partial":
        observation.pop("baselineRuntime")

    class Connection:
        def execute(self, *args: Any) -> Any:
            return self

        def fetchone(self) -> Any:
            return parent

        def fetchall(self) -> Any:
            return rows

    conn = cast(psycopg.Connection[Any], Connection())
    if fault is None:
        validate_runtime(conn, run_id="run", workspace_id="ws", observation=observation)
        validate_runtime(conn, run_id="run", workspace_id="ws", observation={})
    else:
        with pytest.raises(ValueError):
            validate_runtime(conn, run_id="run", workspace_id="ws", observation=observation)
