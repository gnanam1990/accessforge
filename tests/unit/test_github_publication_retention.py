"""Synthetic retention inputs; no GitHub calls or current physical-runtime attestation."""

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

from accessforge_orchestrator import github_publisher as subject
from accessforge_persistence import evaluations
from accessforge_persistence.evidence.objectstore import artifact_key, compute_digest


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "deleted",
        "missing",
        "extra",
        "wrong-producer",
        "wrong-key",
        "changed-bytes",
        "missing-bytes",
        "wrong-evaluation",
    ],
)
def test_completed_check_requires_original_current_retained_bytes(
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    workspace, run, attempt = (str(UUID(int=n)) for n in (1, 2, 3))
    payload = b"original synthetic artifact"
    content_digest = compute_digest(payload)
    row = {
        "id": "original-artifact",
        "kind": "ACTION_TRACE",
        "producer_id": "original-producer",
        "content_digest": content_digest,
        "state": "PROMOTED",
        "retention": "RETAINED",
        "manifest_digest": "a" * 64,
        "size_bytes": len(payload),
        "object_key": artifact_key(
            workspace_id=workspace,
            run_id=run,
            attempt_id=attempt,
            kind="ACTION_TRACE",
            content_digest=content_digest,
        ),
    }
    artifacts = [
        {
            "artifactId": row["id"],
            "kind": row["kind"],
            "producerId": row["producer_id"],
            "digest": content_digest,
        }
    ]
    evaluation = {
        "snapshotDigest": "b" * 64,
        "snapshot": {"attemptId": attempt, "artifacts": artifacts},
    }
    preview = {
        "runStatus": "COMPLETED",
        "evaluationDigest": "b" * 64,
        "identity": {"workspaceId": workspace, "runId": run, "manifestDigest": "a" * 64},
    }
    rows = [row]
    if fault == "deleted":
        row["retention"] = "DELETED"
    elif fault == "missing":
        rows = []
    elif fault == "extra":
        rows = [row, {**row, "id": "unexpected"}]
    elif fault == "wrong-producer":
        row["producer_id"] = "foreign"
    elif fault == "wrong-key":
        row["object_key"] = "another/workspace/key"
    elif fault == "wrong-evaluation":
        preview["evaluationDigest"] = "c" * 64
    reads: list[str] = []

    def get_bounded(*, key: str, max_bytes: int) -> bytes:
        reads.append(key)
        assert max_bytes > len(payload)
        if fault == "missing-bytes":
            raise OSError("synthetic missing bytes")
        return b"changed synthetic artifact" if fault == "changed-bytes" else payload

    monkeypatch.setattr(evaluations, "read", lambda *_args, **_kwargs: evaluation)
    conn = SimpleNamespace(execute=lambda *_args: SimpleNamespace(fetchall=lambda: rows))
    store = SimpleNamespace(get_bounded=get_bounded)
    if fault is None:
        subject._verify_retained(cast(Any, conn), cast(Any, store), preview)
        assert reads == [row["object_key"]]
    else:
        with pytest.raises((subject.PublicationUnconfirmed, OSError)):
            subject._verify_retained(cast(Any, conn), cast(Any, store), preview)
